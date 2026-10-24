"""Deterministic impact and proposal engine owned by Person C.

Pure functions: normalized events, contacts and one signal in; Impact and Plan
records out. No provider calls, no database, no model calls. Every number in
the output can be explained from the inputs, and the explanation is part of
the output (Impact.reason, Plan.evidence_snapshot).

Rules follow section 11 of the master plan:

- Location: earliest arrival = observed time + journey + arrival buffer.
- Flight: earliest arrival = estimated landing + airport exit + journey + arrival buffer.
- Lateness = max(0, earliest arrival - event start); act from 10 minutes.
- Only in-person events with a known destination, starting within 24 hours, can
  trigger a travel-based plan. Remote events are never moved for distance.
  Unknown mode or destination produces an impact that asks for clarification,
  with no plan.
- Up to three candidate times after the earliest arrival, rounded up to 15
  minutes in the event's own time zone, same duration, free on the user's
  calendar, inside the working window. Attendee availability is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

from .models import Action, Contact, Event, Impact, Plan, Signal

LATENESS_THRESHOLD_SECONDS = 10 * 60
RESCHEDULE_THRESHOLD_SECONDS = 45 * 60  # below this, a "running late" note; at or above, propose a new time
PLAN_EXPIRY_SECONDS = 5 * 60
AIRPORT_EXIT_BUFFER_SECONDS = 30 * 60
ARRIVAL_BUFFER_SECONDS = 0
EVALUATION_WINDOW_SECONDS = 24 * 60 * 60
SLOT_SECONDS = 15 * 60
MAX_CANDIDATES = 3
LATEST_LOCAL_START = time(22, 0)  # last start time offered, in the event's time zone
CANDIDATE_LABEL = "free on your calendar; awaiting attendee agreement"

# Live location quality. The API already rejects stale samples; the engine checks
# again so a direct caller can't act on a bad fix.
LOCATION_MAX_AGE_SECONDS = 120
LOCATION_UNCERTAIN_METERS = 200
LOCATION_PRECISE_METERS = 50

INFORMAL_TONES = {"informal", "casual", "friendly", "warm"}
INFORMAL_RELATIONSHIPS = {"friend", "family", "partner"}


@dataclass(frozen=True)
class Arrival:
    at: datetime
    depart_at: datetime  # when the onward journey starts (now, or after landing and exit)
    landing_at: datetime | None  # flights only
    exit_seconds: int
    journey_seconds: int | None  # None when the journey time is unknown


def process(
    signal: Signal,
    events: list[Event],
    contacts: list[Contact],
    now: datetime | None = None,
) -> tuple[list[Impact], list[Plan]]:
    """Return deterministic impacts and plans for one normalized signal."""
    arrival = earliest_arrival(signal)
    if arrival is None or not location_usable(signal, now):
        return [], []

    event = _first_threatened(signal, events, arrival.at)
    if event is None:
        return [], []

    late_seconds = int((arrival.at - event.start_at).total_seconds())
    confidence, confidence_reasons = _confidence(signal, arrival)
    evidence_times = {"signal_observed_at": signal.observed_at, "event_start_at": event.start_at}
    if arrival.landing_at is not None:
        evidence_times["estimated_landing_at"] = arrival.landing_at

    if event.mode != "in_person" or not event.destination:
        missing = "whether it's in person" if event.mode != "in_person" else "where it is"
        impact = Impact(
            id=f"impact_{signal.id}",
            event_id=event.id,
            signal_ids=[signal.id],
            earliest_arrival_at=arrival.at,
            late_by_seconds=late_seconds,
            reason=f"{_explain(arrival, event, late_seconds)}. Confirm {missing} before HeadsUp acts.",
            confidence="low",
            evidence_times=evidence_times,
            status="needs_clarification",
        )
        return [impact], []

    impact = Impact(
        id=f"impact_{signal.id}",
        event_id=event.id,
        signal_ids=[signal.id],
        earliest_arrival_at=arrival.at,
        late_by_seconds=late_seconds,
        reason=_explain(arrival, event, late_seconds),
        confidence=confidence,
        evidence_times=evidence_times,
        status="proposed",
    )

    candidates = candidate_times(event, events, arrival.at)
    contact = next((item for item in contacts if item.id in event.contact_ids), None)
    reschedule = late_seconds >= RESCHEDULE_THRESHOLD_SECONDS
    proposal = candidates[0] if reschedule and candidates else None
    message = draft_message(signal, event, contact, arrival.at, late_seconds, reschedule, proposal)

    action = Action(
        id=f"action_{signal.id}_message_v1",
        type="send_message",
        contact_id=contact.id if contact else None,
        event_id=event.id,
        message_text=message,
        new_start_at=proposal[0] if proposal else None,
        new_end_at=proposal[1] if proposal else None,
    )
    created_at = now or datetime.now(timezone.utc)
    plan = Plan(
        id=f"plan_{signal.id}",
        version=1,
        impact_id=impact.id,
        actions=[action],
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=PLAN_EXPIRY_SECONDS),
        evidence_snapshot={
            "signal_id": signal.id,
            "event_id": event.id,
            "source_mode": signal.source_mode,
            "proposal_kind": "reschedule" if proposal else ("reschedule_no_slot" if reschedule else "running_late"),
            "candidate_times": [
                {"start_at": s.isoformat(), "end_at": e.isoformat(), "label": CANDIDATE_LABEL}
                for s, e in candidates
            ],
            "confidence_reasons": confidence_reasons,
            "recipient_verified": bool(contact),
        },
    )
    return [impact], [plan]


# ---------- arrival ----------

def earliest_arrival(signal: Signal) -> Arrival | None:
    """When the user can be at the destination. None when the signal can't say."""
    journey = signal.route_duration_seconds
    if signal.kind.startswith("flight"):
        if signal.estimated_arrival_at is None:
            return None  # a missing estimate is unknown, never "no delay"
        at = signal.estimated_arrival_at + timedelta(
            seconds=AIRPORT_EXIT_BUFFER_SECONDS + (journey or 0) + ARRIVAL_BUFFER_SECONDS)
        depart = signal.estimated_arrival_at + timedelta(seconds=AIRPORT_EXIT_BUFFER_SECONDS)
        return Arrival(at, depart, signal.estimated_arrival_at, AIRPORT_EXIT_BUFFER_SECONDS, journey)
    if signal.estimated_arrival_at is not None:
        at = signal.estimated_arrival_at + timedelta(seconds=(journey or 0) + ARRIVAL_BUFFER_SECONDS)
        return Arrival(at, signal.estimated_arrival_at, None, 0, journey)
    if journey is not None:
        at = signal.observed_at + timedelta(seconds=journey + ARRIVAL_BUFFER_SECONDS)
        return Arrival(at, signal.observed_at, None, 0, journey)
    return None


def location_usable(signal: Signal, now: datetime | None) -> bool:
    """Live phone positions must be fresh and reasonably accurate. Demo (mock) input is labeled and trusted."""
    if signal.source_mode != "live" or signal.position is None:
        return True
    accuracy = signal.position.accuracy_meters
    if accuracy is not None and accuracy > LOCATION_UNCERTAIN_METERS:
        return False
    if now is not None and (now - signal.observed_at).total_seconds() > LOCATION_MAX_AGE_SECONDS:
        return False
    return True


def _first_threatened(signal: Signal, events: list[Event], arrival: datetime) -> Event | None:
    """The earliest upcoming event the user would reach 10+ minutes late. Remote events never count."""
    threatened = [
        event for event in events
        if event.mode != "remote"
        and event.start_at >= signal.observed_at
        and (event.start_at - signal.observed_at).total_seconds() <= EVALUATION_WINDOW_SECONDS
        and (arrival - event.start_at).total_seconds() >= LATENESS_THRESHOLD_SECONDS
    ]
    return min(threatened, key=lambda event: event.start_at, default=None)


def _confidence(signal: Signal, arrival: Arrival) -> tuple[str, list[str]]:
    """A rule-based label with the rules that set it, not a probability."""
    reasons: list[str] = []
    if signal.source_mode == "mock":
        reasons.append("demo input, labeled as simulated")
    else:
        reasons.append(f"live {signal.source}")
    if arrival.journey_seconds is None:
        reasons.append("journey time unknown; arrival assumes no onward travel")
    if signal.position is not None and signal.position.accuracy_meters is not None:
        if signal.position.accuracy_meters > LOCATION_PRECISE_METERS:
            reasons.append(f"location accurate to {round(signal.position.accuracy_meters)} m")
    downgraded = arrival.journey_seconds is None or (
        signal.position is not None
        and signal.position.accuracy_meters is not None
        and signal.position.accuracy_meters > LOCATION_PRECISE_METERS
    )
    return ("medium" if downgraded else "high"), reasons


def _explain(arrival: Arrival, event: Event, late_seconds: int) -> str:
    tz = event.timezone
    start = _local_time(event.start_at, tz)
    late = f"{round(late_seconds / 60)} minutes after {event.title} starts at {start}"
    if arrival.landing_at is not None:
        journey = (f"a {round(arrival.journey_seconds / 60)} minute journey" if arrival.journey_seconds
                   else "an unknown journey time")
        return (f"The flight is expected at {_local_time(arrival.landing_at, tz)}; with a "
                f"{round(arrival.exit_seconds / 60)} minute airport exit and {journey}, the earliest arrival "
                f"is {_local_time(arrival.at, tz)}, {late}")
    journey = round((arrival.journey_seconds or 0) / 60)
    return (f"A {journey} minute journey at {_local_time(arrival.depart_at, tz)} reaches {event.title} at "
            f"{_local_time(arrival.at, tz)}, {round(late_seconds / 60)} minutes after it starts at {start}")


# ---------- candidate times ----------

def candidate_times(event: Event, events: list[Event], arrival: datetime,
                    limit: int = MAX_CANDIDATES) -> list[tuple[datetime, datetime]]:
    """Up to `limit` (start, end) slots after arrival: same duration, no overlap with other
    timed events, starting no later than LATEST_LOCAL_START on the event's local day."""
    duration = event.end_at - event.start_at
    tz = ZoneInfo(event.timezone)
    local_day = event.start_at.astimezone(tz).date()
    latest = datetime.combine(local_day, LATEST_LOCAL_START, tzinfo=tz)
    others = [item for item in events if item.id != event.id]

    found: list[tuple[datetime, datetime]] = []
    start = round_up(arrival, event.timezone)
    while start <= latest and len(found) < limit:
        end = start + duration
        clash = next((item for item in others if item.start_at < end and item.end_at > start), None)
        if clash is None:
            found.append((start, end))
            start = start + timedelta(seconds=SLOT_SECONDS)
        else:
            start = round_up(clash.end_at, event.timezone)
    return found


def round_up(value: datetime, timezone_name: str, seconds: int = SLOT_SECONDS) -> datetime:
    """Round up to the next slot boundary on the local clock (correct for :30 and :45 offset zones)."""
    local = value.astimezone(ZoneInfo(timezone_name))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (local - midnight).total_seconds()
    return (midnight + timedelta(seconds=ceil(elapsed / seconds) * seconds)).astimezone(timezone.utc)


# ---------- message ----------

def draft_message(signal: Signal, event: Event, contact: Contact | None, arrival: datetime,
                  late_seconds: int, reschedule: bool, proposal: tuple[datetime, datetime] | None) -> str:
    """Fixed templates. Only the context needed: no itinerary, no location, no unrelated plans."""
    informal = contact is not None and (
        contact.tone.lower() in INFORMAL_TONES or contact.relationship.lower() in INFORMAL_RELATIONSHIPS)
    name = contact.display_name if contact else None
    minutes = _round_minutes(late_seconds)
    eta = _local_time(arrival, event.timezone)
    cause = "My flight is delayed" if signal.kind.startswith("flight") else "I'm running behind"

    if not reschedule:
        if informal:
            body = f"I'm running about {minutes} minutes late. ETA around {eta}—sorry!"
        else:
            body = f"I'm running about {minutes} minutes late and expect to arrive around {eta}. Apologies for the delay."
    elif proposal is None:
        if informal:
            body = f"{cause} and I'll be about {minutes} minutes late. Could we find another time?"
        else:
            body = f"{cause}, and I'll be about {minutes} minutes late for {event.title}. Could we find another time?"
    else:
        new_start = _local_time(proposal[0], event.timezone)
        if informal:
            title = event.title[:1].lower() + event.title[1:]
            body = f"{cause} and I'll be about {minutes} minutes late. Could we move {title} to {new_start}?"
        else:
            body = (f"{cause}, and I'll be about {minutes} minutes late for {event.title}. "
                    f"Would {new_start} work instead?")
    return _with_greeting(name, informal, body)


def _with_greeting(name: str | None, informal: bool, body: str) -> str:
    """'Hey Sam, ' + body, lowercasing the body's first word unless it's "I"."""
    if not name:
        return body
    if not (body.startswith("I ") or body.startswith("I'")):
        body = body[:1].lower() + body[1:]
    return f"{'Hey' if informal else 'Hi'} {name}, {body}"


def _round_minutes(seconds: int) -> int:
    """'About 40 minutes': nearest 5 above 15 minutes, so the message doesn't sound falsely precise."""
    minutes = ceil(seconds / 60)
    return minutes if minutes < 15 else int(5 * round(minutes / 5))


def _local_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%-I:%M %p")
