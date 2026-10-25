"""Deterministic inbound scheduling requests from verified Beeper chats."""

from __future__ import annotations

import re
from typing import Any
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .engine import PLAN_EXPIRY_SECONDS, round_up
from .models import Action, Contact, Event, Impact, IncomingMessage, Plan, ScheduleRequest
from .db import FoundationRepository
from .messaging import BeeperMessaging, MessagingProvider

REQUEST_RE = re.compile(
    r"\b(?P<title>dinner|lunch|coffee|meet(?:ing)?)\b.*?\b(?P<day>today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r".*?\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b"
    r"(?:.*?\bfor\s+(?P<duration>\d+)\s*(?:minutes?|mins?|min))?",
    re.IGNORECASE,
)
WEEKDAYS = {name: index for index, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}


class ScheduleParseError(ValueError):
    pass


def parse_request(message: IncomingMessage, contact: Contact, timezone_name: str) -> ScheduleRequest:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleParseError("invalid timezone") from exc
    match = REQUEST_RE.search(message.text)
    if not match:
        raise ScheduleParseError("message needs an activity, day and explicit time")
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    ampm = (match["ampm"] or "").lower()
    if ampm:
        if not 1 <= hour <= 12:
            raise ScheduleParseError("invalid 12-hour time")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif 1 <= hour <= 7:
        hour += 12  # demo grammar convention: conversational 1–7 means afternoon/evening
    elif not 0 <= hour <= 23:
        raise ScheduleParseError("invalid time")
    elif 8 <= hour <= 12:
        raise ScheduleParseError("time needs AM or PM")
    local_received = message.received_at.astimezone(tz)
    day = match["day"].lower()
    if day == "today":
        date = local_received.date()
    elif day == "tomorrow":
        date = local_received.date() + timedelta(days=1)
    else:
        delta = (WEEKDAYS[day] - local_received.weekday()) % 7
        date = local_received.date() + timedelta(days=delta or 7)
    start = datetime.combine(date, time(hour, minute), tzinfo=tz)
    if start <= local_received:
        raise ScheduleParseError("requested time is in the past")
    duration = int(match["duration"] or 60)
    if not 15 <= duration <= 240:
        raise ScheduleParseError("duration must be between 15 and 240 minutes")
    title = match["title"].lower()
    title = "Meeting" if title.startswith("meet") else title.title()
    return ScheduleRequest(
        id=f"schedule_{message.id}", message_id=message.id, contact_id=contact.id,
        title=f"{title} with {contact.display_name}", requested_start_at=start.astimezone(timezone.utc),
        requested_end_at=(start + timedelta(minutes=duration)).astimezone(timezone.utc), timezone=timezone_name,
    )


def build_plan(request: ScheduleRequest, message: IncomingMessage, contact: Contact,
               events: list[Event], now: datetime | None = None) -> tuple[Impact, Plan]:
    conflicts = [event for event in events
                 if event.start_at < request.requested_end_at and event.end_at > request.requested_start_at]
    duration = request.requested_end_at - request.requested_start_at
    candidates: list[tuple[datetime, datetime]] = []
    cursor = request.requested_start_at
    while len(candidates) < 3:
        overlapping = [event for event in events if event.start_at < cursor + duration and event.end_at > cursor]
        if overlapping:
            cursor = round_up(max(overlapping, key=lambda item: item.end_at).end_at, request.timezone)
            continue
        candidates.append((cursor, cursor + duration))
        cursor += timedelta(minutes=15)
    candidate, end = candidates[0]
    conflict = conflicts[0] if conflicts else None
    reason = (f"{contact.display_name} requested {request.title} during {conflict.title}; "
              f"the first free time is {candidate.astimezone(ZoneInfo(request.timezone)).strftime('%-I:%M %p')}."
              if conflict else f"{contact.display_name}'s requested time is free.")
    impact = Impact(
        id=f"impact_{message.id}", event_id=conflict.id if conflict else "requested_time",
        signal_ids=[], earliest_arrival_at=candidate,
        late_by_seconds=int((candidate - request.requested_start_at).total_seconds()),
        reason=reason, confidence="high", evidence_times={"message_received_at": message.received_at},
        status="proposed",
    )
    local = candidate.astimezone(ZoneInfo(request.timezone)).strftime("%-I:%M %p")
    text = (f"I already have {conflict.title} then. Could we do {local} instead?"
            if conflict else f"{local} works for me.")
    create = Action(
        id=f"action_{message.id}_calendar_v1", type="create_event", contact_id=contact.id,
        event_title=f"Tentative: {request.title}", new_start_at=candidate, new_end_at=end,
        timezone=request.timezone, tentative=True,
    )
    reply = Action(
        id=f"action_{message.id}_message_v1", type="send_message", contact_id=contact.id,
        message_text=text, new_start_at=candidate, new_end_at=end,
        depends_on_action_ids=[create.id],
    )
    created = now or datetime.now(timezone.utc)
    plan = Plan(
        id=f"plan_{message.id}", version=1, impact_id=impact.id, actions=[create, reply],
        created_at=created, expires_at=created + timedelta(seconds=PLAN_EXPIRY_SECONDS),
        evidence_snapshot={"incoming_message_id": message.id, "schedule_request_id": request.id,
                           "conflict_event_ids": [item.id for item in conflicts],
                           "proposal_kind": "inbound_schedule",
                           "candidate_times": [{"start_at": start.isoformat(), "end_at": finish.isoformat()}
                                               for start, finish in candidates]},
    )
    return impact, plan


class InboundScheduler:
    def __init__(self, repository: FoundationRepository, messaging: MessagingProvider,
                 timezone_name: str = "America/New_York"):
        self.repository = repository
        self.messaging = messaging
        self.timezone_name = timezone_name
        self.last_successful_poll: datetime | None = None
        self.last_error: str | None = None

    async def poll(self, supplied: list[dict[str, Any]] | None = None) -> list[IncomingMessage]:
        raw_messages: list[tuple[Contact | None, dict[str, Any]]] = []
        contacts = self.repository.session().contacts
        if supplied is not None:
            for raw in supplied:
                contact = next((item for item in contacts if item.id == raw.get("contact_id")), None)
                raw_messages.append((contact, raw))
        elif isinstance(self.messaging, BeeperMessaging):
            try:
                for contact in contacts:
                    if not contact.beeper_chat_id:
                        continue
                    for raw in await self.messaging.read_recent_messages(contact.beeper_chat_id):
                        if not raw.get("isSender"):
                            raw_messages.append((contact, raw))
            except Exception as exc:
                self.last_error = type(exc).__name__
                raise
        else:
            return []

        processed: list[IncomingMessage] = []
        for contact, raw in raw_messages:
            message_id = str(raw.get("id") or "")
            text = str(raw.get("text") or "").strip()
            if not message_id or not text or self.repository.get("incoming_messages", message_id):
                continue
            received = _message_time(raw.get("timestamp"))
            message = IncomingMessage(
                id=message_id, chat_id=str(raw.get("chat_id") or (contact.beeper_chat_id if contact else "unknown")),
                contact_id=contact.id if contact else None, sender_name=raw.get("senderName"), text=text,
                received_at=received,
            )
            if contact is None:
                message = message.model_copy(update={"status": "unprocessed", "error_code": "INVALID_RECIPIENT"})
            else:
                try:
                    request = parse_request(message, contact, self.timezone_name)
                    impact, plan = build_plan(request, message, contact, self.repository.session().events)
                    with self.repository.transaction():
                        self.repository.put("schedule_requests", request)
                        self.repository.put("impacts", impact)
                        self.repository.put_plan(plan)
                    message = message.model_copy(update={"status": "processed"})
                except ScheduleParseError as exc:
                    message = message.model_copy(update={"status": "ambiguous", "error_code": str(exc)})
            self.repository.put("incoming_messages", message)
            processed.append(message)
        self.last_successful_poll = datetime.now(timezone.utc)
        self.last_error = None
        return processed

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.last_error is None else "degraded",
            "last_successful_poll": self.last_successful_poll.isoformat() if self.last_successful_poll else None,
            "last_error": self.last_error,
        }


def _message_time(value: Any) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)
