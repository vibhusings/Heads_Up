from datetime import datetime, timezone

import httpx
import pytest

from headsup.models import Event
from headsup.providers.calendar import (
    WRITE_SCOPE,
    CalendarChanges,
    CalendarWindow,
    GoogleCalendarProvider,
    MockCalendarProvider,
    normalize_google_event,
)


def dt(hour: int) -> datetime:
    return datetime(2026, 10, 25, hour, tzinfo=timezone.utc)


def event(*, organizer_self: bool = True, version: str = "v1") -> Event:
    return Event(
        id="event_1",
        provider="mock",
        provider_event_id="provider_1",
        title="Dinner",
        start_at=dt(2),
        end_at=dt(3),
        timezone="America/Los_Angeles",
        organizer_self=organizer_self,
        writable=organizer_self,
        provider_version=version,
    )


def changes() -> CalendarChanges:
    return CalendarChanges(new_start_at=dt(4), new_end_at=dt(5), timezone="America/Los_Angeles")


def test_google_normalization_excludes_all_day_and_preserves_timezone():
    assert normalize_google_event(
        {"id": "all-day", "start": {"date": "2026-10-25"}, "end": {"date": "2026-10-26"}},
        "America/Los_Angeles",
    ) is None
    normalized = normalize_google_event(
        {
            "id": "timed",
            "etag": "v1",
            "summary": "Dinner",
            "location": "Cafe",
            "organizer": {"self": True},
            "start": {"dateTime": "2026-10-24T19:00:00-07:00", "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": "2026-10-24T20:00:00-07:00", "timeZone": "America/Los_Angeles"},
        },
        "UTC",
    )
    assert normalized is not None
    assert normalized.start_at.astimezone(timezone.utc) == dt(2)
    assert normalized.timezone == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_mock_calendar_rejects_stale_and_external_organizer_updates():
    stale = await MockCalendarProvider([event()]).update_event("event_1", "old", changes())
    assert stale.error_code == "STALE_PLAN"

    external = await MockCalendarProvider([event(organizer_self=False)]).update_event("event_1", "v1", changes())
    assert external.error_code == "PERMISSION_DENIED"
    assert external.request_required is True

    provider = MockCalendarProvider([event()])
    updated = await provider.update_event("event_1", "v1", changes())
    assert updated.status == "succeeded"
    assert updated.event and updated.event.start_at == dt(4)


class TokenStore:
    scopes = [WRITE_SCOPE]

    async def access_token(self) -> str:
        return "test-token"


@pytest.mark.asyncio
async def test_google_list_expands_instances_and_skips_all_day():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["singleEvents"] == "true"
        return httpx.Response(
            200,
            json={
                "timeZone": "America/Los_Angeles",
                "items": [
                    {"id": "all-day", "start": {"date": "2026-10-25"}, "end": {"date": "2026-10-26"}},
                    {
                        "id": "timed",
                        "etag": "v1",
                        "summary": "Dinner",
                        "organizer": {"self": True},
                        "start": {"dateTime": "2026-10-24T19:00:00-07:00"},
                        "end": {"dateTime": "2026-10-24T20:00:00-07:00"},
                    },
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleCalendarProvider("primary", TokenStore(), client=client, scopes=[WRITE_SCOPE])
    events = await provider.list_events(CalendarWindow(start_at=dt(0), end_at=dt(6)))
    assert [item.id for item in events] == ["google:timed"]
    await client.aclose()


@pytest.mark.asyncio
async def test_google_write_rechecks_version_and_external_organizer():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(
            200,
            json={
                "id": "provider_1",
                "etag": "v1",
                "organizer": {"self": False},
                "start": {"dateTime": "2026-10-24T19:00:00-07:00"},
                "end": {"dateTime": "2026-10-24T20:00:00-07:00"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleCalendarProvider("primary", TokenStore(), client=client, scopes=[WRITE_SCOPE])
    result = await provider.update_event("provider_1", "v1", changes())
    assert result.error_code == "PERMISSION_DENIED"
    assert result.request_required is True
    assert requests == ["GET"]
    await client.aclose()
