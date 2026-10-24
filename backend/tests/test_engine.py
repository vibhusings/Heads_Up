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
