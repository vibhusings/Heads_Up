"""Mock and Google Calendar adapters with conservative write semantics."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import AwareDatetime, Event

READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"


class CalendarProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retry_safe: bool = False):
        super().__init__(message)
        self.code = code
        self.retry_safe = retry_safe


class CalendarWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "CalendarWindow":
        if self.end_at <= self.start_at:
            raise ValueError("calendar window end must be after start")
        return self


class CalendarChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_start_at: AwareDatetime
    new_end_at: AwareDatetime
    timezone: str

    @model_validator(mode="after")
    def ordered(self) -> "CalendarChanges":
        if self.new_end_at <= self.new_start_at:
            raise ValueError("event end must be after start")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        return self


class AvailabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "busy", "unknown"]
    busy: dict[str, list[CalendarWindow]] = Field(default_factory=dict)
    error_code: str | None = None


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed", "unknown"]
    event: Event | None = None
    provider_reference: str | None = None
    error_code: str | None = None
    retry_safe: bool = False
    request_required: bool = False


class CalendarProvider(Protocol):
    mode: str

    async def list_events(self, window: CalendarWindow) -> list[Event]: ...
    async def get_availability(self, calendar_ids: list[str], window: CalendarWindow) -> AvailabilityResult: ...
    async def update_event(
        self, event_id: str, expected_version: str, changes: CalendarChanges
    ) -> ProviderResult: ...
    def health(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class MockCalendarProvider:
    mode = "mock"

    def __init__(self, events: list[Event]):
        self.events = {event.id: event.model_copy(deep=True) for event in events}

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "ok"}

    async def close(self) -> None:
        return None

    async def list_events(self, window: CalendarWindow) -> list[Event]:
        return [
            event.model_copy(deep=True)
            for event in self.events.values()
            if event.start_at < window.end_at and event.end_at > window.start_at
        ]

    async def get_availability(self, calendar_ids: list[str], window: CalendarWindow) -> AvailabilityResult:
        busy = {
            calendar_id: [
                CalendarWindow(start_at=event.start_at, end_at=event.end_at)
                for event in await self.list_events(window)
            ]
            for calendar_id in calendar_ids
        }
        return AvailabilityResult(status="busy" if any(busy.values()) else "available", busy=busy)

    async def update_event(
        self, event_id: str, expected_version: str, changes: CalendarChanges
    ) -> ProviderResult:
        event = self.events.get(event_id)
        if event is None:
            return ProviderResult(status="failed", error_code="PROVIDER_UNAVAILABLE", retry_safe=False)
        if event.provider_version != expected_version:
            return ProviderResult(status="failed", error_code="STALE_PLAN", retry_safe=True)
        if not event.organizer_self or not event.writable:
            return ProviderResult(
                status="failed", error_code="PERMISSION_DENIED", retry_safe=False, request_required=True
            )
        version = f"{expected_version}-updated"
        updated = event.model_copy(
            update={
                "start_at": changes.new_start_at,
                "end_at": changes.new_end_at,
                "timezone": changes.timezone,
                "provider_version": version,
            }
        )
        self.events[event_id] = updated
        return ProviderResult(status="succeeded", event=updated, provider_reference=event_id)


class UnavailableCalendarProvider:
    mode = "live"

    def __init__(self, reason: str = "Google Calendar OAuth is not configured"):
        self.reason = reason

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "auth_required", "error_code": "AUTH_REQUIRED"}

    async def close(self) -> None:
        return None

    async def list_events(self, window: CalendarWindow) -> list[Event]:
        raise CalendarProviderError("AUTH_REQUIRED", self.reason)

    async def get_availability(self, calendar_ids: list[str], window: CalendarWindow) -> AvailabilityResult:
        return AvailabilityResult(status="unknown", error_code="AUTH_REQUIRED")

    async def update_event(
        self, event_id: str, expected_version: str, changes: CalendarChanges
    ) -> ProviderResult:
        return ProviderResult(status="failed", error_code="AUTH_REQUIRED", retry_safe=False)


class GoogleTokenStore:
    """Loads and refreshes user OAuth credentials from an untracked token file."""

    def __init__(self, token_file: Path, scopes: list[str]):
        self.token_file = token_file
        self.scopes = scopes

    async def access_token(self) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:  # pragma: no cover - installation error
            raise CalendarProviderError("AUTH_REQUIRED", "Google OAuth dependencies are not installed") from exc
        try:
            credentials = Credentials.from_authorized_user_file(str(self.token_file), self.scopes)
        except (OSError, ValueError) as exc:
            raise CalendarProviderError("AUTH_REQUIRED", "Google Calendar token file is unavailable") from exc
        if not credentials.valid:
            if not credentials.refresh_token:
                raise CalendarProviderError("AUTH_REQUIRED", "Google Calendar authorization must be renewed")
            try:
                await asyncio.to_thread(credentials.refresh, Request())
            except Exception as exc:
                raise CalendarProviderError("AUTH_REQUIRED", "Google Calendar token refresh failed") from exc
            temp = self.token_file.with_suffix(self.token_file.suffix + ".tmp")
            temp.write_text(credentials.to_json(), encoding="utf-8")
            os.chmod(temp, 0o600)
            temp.replace(self.token_file)
        if not credentials.token:
            raise CalendarProviderError("AUTH_REQUIRED", "Google Calendar access token is unavailable")
        return credentials.token


class GoogleCalendarProvider:
    mode = "live"
    base_url = "https://www.googleapis.com/calendar/v3"

    def __init__(
        self,
        calendar_id: str,
        token_store: GoogleTokenStore,
        client: httpx.AsyncClient | None = None,
        scopes: list[str] | None = None,
    ):
        self.calendar_id = calendar_id
        self.token_store = token_store
        self.client = client or httpx.AsyncClient(timeout=15)
        self._owns_client = client is None
        self.scopes = set(scopes or token_store.scopes)

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "status": "configured"}

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = await self.token_store.access_token()
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        try:
            response = await self.client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise CalendarProviderError("OUTCOME_UNKNOWN", "Google Calendar request timed out") from exc
        if response.status_code in {401, 403}:
            raise CalendarProviderError("PERMISSION_DENIED", "Google Calendar denied the request")
        if response.status_code == 412:
            raise CalendarProviderError("STALE_PLAN", "Google Calendar event changed", retry_safe=True)
        if response.status_code == 429:
            raise CalendarProviderError("RATE_LIMITED", "Google Calendar rate limit reached", retry_safe=True)
        if response.status_code >= 500:
            raise CalendarProviderError("PROVIDER_UNAVAILABLE", "Google Calendar is unavailable", retry_safe=True)
        response.raise_for_status()
        return response

    async def list_events(self, window: CalendarWindow) -> list[Event]:
        path = f"/calendars/{quote(self.calendar_id, safe='')}/events"
        params: dict[str, str | int | bool] = {
            "timeMin": _rfc3339(window.start_at),
            "timeMax": _rfc3339(window.end_at),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 250,
        }
        events: list[Event] = []
        while True:
            data = (await self._request("GET", path, params=params)).json()
            calendar_timezone = data.get("timeZone") or "UTC"
            for raw in data.get("items", []):
                event = normalize_google_event(raw, calendar_timezone)
                if event is not None:
                    events.append(event)
            token = data.get("nextPageToken")
            if not token:
                return events
            params["pageToken"] = token

    async def get_availability(self, calendar_ids: list[str], window: CalendarWindow) -> AvailabilityResult:
        if FREEBUSY_SCOPE not in self.scopes:
            return AvailabilityResult(status="unknown", error_code="PERMISSION_DENIED")
        response = await self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": _rfc3339(window.start_at),
                "timeMax": _rfc3339(window.end_at),
                "items": [{"id": item} for item in calendar_ids],
            },
        )
        busy: dict[str, list[CalendarWindow]] = {}
        unknown = False
        for calendar_id, value in response.json().get("calendars", {}).items():
            if value.get("errors"):
                unknown = True
                continue
            busy[calendar_id] = [
                CalendarWindow(start_at=_parse_datetime(item["start"]), end_at=_parse_datetime(item["end"]))
                for item in value.get("busy", [])
            ]
        status = "unknown" if unknown else ("busy" if any(busy.values()) else "available")
        return AvailabilityResult(status=status, busy=busy, error_code="PROVIDER_UNAVAILABLE" if unknown else None)

    async def update_event(
        self, event_id: str, expected_version: str, changes: CalendarChanges
    ) -> ProviderResult:
        if WRITE_SCOPE not in self.scopes:
            return ProviderResult(status="failed", error_code="PERMISSION_DENIED", retry_safe=False)
        path = f"/calendars/{quote(self.calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        try:
            current = (await self._request("GET", path)).json()
            if current.get("etag") != expected_version:
                return ProviderResult(status="failed", error_code="STALE_PLAN", retry_safe=True)
            if current.get("recurrence") or current.get("recurringEventId"):
                return ProviderResult(status="failed", error_code="PERMISSION_DENIED", retry_safe=False)
            if not current.get("organizer", {}).get("self", False):
                return ProviderResult(
                    status="failed", error_code="PERMISSION_DENIED", retry_safe=False, request_required=True
                )
            payload = {
                "start": {"dateTime": _rfc3339(changes.new_start_at), "timeZone": changes.timezone},
                "end": {"dateTime": _rfc3339(changes.new_end_at), "timeZone": changes.timezone},
            }
            updated = (
                await self._request(
                    "PATCH",
                    path,
                    params={"sendUpdates": "all"},
                    headers={"If-Match": expected_version},
                    json=payload,
                )
            ).json()
            event = normalize_google_event(updated, changes.timezone)
            if event is None:
                raise CalendarProviderError("PROVIDER_UNAVAILABLE", "Google returned an invalid event")
            return ProviderResult(status="succeeded", event=event, provider_reference=event_id)
        except CalendarProviderError as exc:
            return ProviderResult(status="unknown" if exc.code == "OUTCOME_UNKNOWN" else "failed", error_code=exc.code,
                                  retry_safe=exc.retry_safe)


def normalize_google_event(raw: dict[str, Any], calendar_timezone: str) -> Event | None:
    """Convert a timed Google event to the shared contract; all-day events are excluded."""
    start_raw, end_raw = raw.get("start", {}), raw.get("end", {})
    if "dateTime" not in start_raw or "dateTime" not in end_raw:
        return None
    start, end = _parse_datetime(start_raw["dateTime"]), _parse_datetime(end_raw["dateTime"])
    event_timezone = start_raw.get("timeZone") or calendar_timezone
    try:
        ZoneInfo(event_timezone)
    except ZoneInfoNotFoundError as exc:
        raise CalendarProviderError("PROVIDER_UNAVAILABLE", "Google returned an invalid timezone") from exc
    organizer_self = bool(raw.get("organizer", {}).get("self", False))
    return Event(
        id=f"google:{raw['id']}",
        provider="google",
        provider_event_id=str(raw["id"]),
        title=raw.get("summary") or "Busy",
        start_at=start,
        end_at=end,
        timezone=event_timezone,
        mode="in_person" if raw.get("location") else "unknown",
        destination=raw.get("location"),
        contact_ids=[],
        organizer_self=organizer_self,
        writable=organizer_self and raw.get("status") != "cancelled",
        provider_version=raw.get("etag"),
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
