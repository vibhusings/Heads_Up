"""Deterministic Level A impact and proposal engine owned by Person C."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

from .models import Action, Contact, Event, Impact, Plan, Signal

LATENESS_THRESHOLD_SECONDS = 10 * 60
PLAN_EXPIRY_SECONDS = 5 * 60


def process(
    signal: Signal,
    events: list[Event],
    contacts: list[Contact],
    now: datetime | None = None,
) -> tuple[list[Impact], list[Plan]]:
    """Return deterministic impacts/plans for one normalized signal."""
    arrival = _arrival(signal)
    if arrival is None:
        return [], []
    candidates = sorted(
        (
            event
            for event in events
            if event.mode == "in_person"
            and event.start_at >= signal.observed_at
            and (arrival - event.start_at).total_seconds() >= LATENESS_THRESHOLD_SECONDS
        ),
        key=lambda event: event.start_at,
    )
    if not candidates:
        return [], []

    event = candidates[0]
    late_seconds = int((arrival - event.start_at).total_seconds())
    impact = Impact(
        id=f"impact_{signal.id}",
        event_id=event.id,
        signal_ids=[signal.id],
        earliest_arrival_at=arrival,
        late_by_seconds=late_seconds,
        reason=(
            f"Earliest arrival is {_local_time(arrival, event.timezone)}, "
            f"{late_seconds // 60} minutes after {event.title} starts"
        ),
        confidence="high",
        evidence_times={"signal_observed_at": signal.observed_at},
        status="proposed",
    )
    new_start = _round_up(arrival, 15 * 60)
    new_end = new_start + (event.end_at - event.start_at)
    contact = next((item for item in contacts if item.id in event.contact_ids), None)
    greeting = f"Hey {contact.display_name}, " if contact else ""
    message = (
        f"{greeting}I’m running about {ceil(late_seconds / 60)} minutes late. "
        f"Could we move {event.title} to {_local_time(new_start, event.timezone)}?"
    )
    action = Action(
        id=f"action_{signal.id}_message_v1",
        type="send_message",
        contact_id=contact.id if contact else None,
        event_id=event.id,
        message_text=message,
        new_start_at=new_start,
        new_end_at=new_end,
    )
    created_at = now or datetime.now(timezone.utc)
    plan = Plan(
        id=f"plan_{signal.id}",
        version=1,
        impact_id=impact.id,
        actions=[action],
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=PLAN_EXPIRY_SECONDS),
        evidence_snapshot={"signal_id": signal.id, "event_id": event.id},
    )
    return [impact], [plan]


def _arrival(signal: Signal) -> datetime | None:
    if signal.estimated_arrival_at is not None:
        return signal.estimated_arrival_at
    if signal.route_duration_seconds is not None:
        return signal.observed_at + timedelta(seconds=signal.route_duration_seconds)
    return None


def _round_up(value: datetime, seconds: int) -> datetime:
    timestamp = value.astimezone(timezone.utc).timestamp()
    return datetime.fromtimestamp(ceil(timestamp / seconds) * seconds, tz=timezone.utc)


def _local_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%-I:%M %p")
