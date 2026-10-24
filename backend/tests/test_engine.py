from datetime import datetime, timezone

from headsup.engine import process
from headsup.models import Contact, Event, Signal


def test_location_signal_creates_expected_impact_and_proposal():
    event = Event(
        id="dinner",
        provider="fixture",
        title="Dinner with Sam",
        start_at=datetime(2026, 10, 25, 2, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc),
        timezone="America/Los_Angeles",
        mode="in_person",
        destination="Cafe",
        contact_ids=["sam"],
    )
    contact = Contact(
        id="sam",
        display_name="Sam",
        relationship="friend",
        preferred_channel="message",
        tone="informal",
    )
    signal = Signal(
        id="location",
        kind="location_eta",
        source="fixture",
        source_mode="mock",
        observed_at=datetime(2026, 10, 25, 1, 40, tzinfo=timezone.utc),
        route_duration_seconds=3600,
    )

    impacts, plans = process(signal, [event], [contact], now=signal.observed_at)

    assert impacts[0].late_by_seconds == 2400
    assert plans[0].actions[0].new_start_at == datetime(2026, 10, 25, 2, 45, tzinfo=timezone.utc)
    assert "Sam" in plans[0].actions[0].message_text


def test_flight_signal_adds_exit_and_route_time_and_ignores_later_event():
    meeting = Event(
        id="meeting",
        provider="fixture",
        title="Meeting",
        start_at=datetime(2026, 10, 24, 16, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 24, 17, tzinfo=timezone.utc),
        timezone="UTC",
        mode="in_person",
        destination="Office",
    )
    later = meeting.model_copy(
        update={
            "id": "later",
            "title": "Later meeting",
            "start_at": datetime(2026, 10, 24, 18, tzinfo=timezone.utc),
            "end_at": datetime(2026, 10, 24, 19, tzinfo=timezone.utc),
        }
    )
    signal = Signal(
        id="flight",
        kind="flight_delay",
        source="fixture",
        source_mode="mock",
        observed_at=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
        estimated_arrival_at=datetime(2026, 10, 24, 15, 45, tzinfo=timezone.utc),
        route_duration_seconds=35 * 60,
    )

    impacts, _ = process(signal, [meeting, later], [], now=signal.observed_at)

    assert impacts[0].event_id == "meeting"
    assert impacts[0].earliest_arrival_at == datetime(2026, 10, 24, 16, 50, tzinfo=timezone.utc)
    assert impacts[0].late_by_seconds == 50 * 60


def test_remote_event_is_not_disrupted_and_candidate_avoids_conflict():
    dinner = Event(
        id="dinner",
        provider="fixture",
        title="Dinner",
        start_at=datetime(2026, 10, 25, 2, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc),
        timezone="UTC",
        mode="in_person",
        destination="Cafe",
    )
    conflict = Event(
        id="conflict",
        provider="fixture",
        title="Busy",
        start_at=datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 25, 4, tzinfo=timezone.utc),
        timezone="UTC",
        mode="remote",
    )
    signal = Signal(
        id="location",
        kind="location_eta",
        source="fixture",
        source_mode="mock",
        observed_at=datetime(2026, 10, 25, 1, 40, tzinfo=timezone.utc),
        route_duration_seconds=3600,
    )

    _, plans = process(signal, [dinner, conflict], [], now=signal.observed_at)

    assert plans[0].actions[0].new_start_at == datetime(2026, 10, 25, 4, tzinfo=timezone.utc)
