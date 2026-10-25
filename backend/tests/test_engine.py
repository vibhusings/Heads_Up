"""Engine rules from sections 11 and 14 of the master plan."""

from datetime import datetime, timedelta, timezone

from headsup.engine import candidate_times, process, round_up
from headsup.models import Contact, Event, Signal

UTC = timezone.utc
SAM = Contact(id="sam", display_name="Sam", relationship="friend", preferred_channel="message", tone="informal")
ALEX = Contact(id="alex", display_name="Alex", relationship="colleague", preferred_channel="message",
               tone="professional")


def event(id_="dinner", title="Dinner with Sam", start=datetime(2026, 10, 25, 2, tzinfo=UTC), minutes=90,
          tz="America/Los_Angeles", mode="in_person", destination="Little Serow", contacts=("sam",)):
    return Event(id=id_, provider="fixture", title=title, start_at=start, end_at=start + timedelta(minutes=minutes),
                 timezone=tz, mode=mode, destination=destination, contact_ids=list(contacts))


def location(observed=datetime(2026, 10, 25, 1, 40, tzinfo=UTC), journey_min=60, mode="mock", accuracy=None,
             id_="location"):
    position = None if accuracy is None else {"latitude": 38.9, "longitude": -77.0, "accuracy_meters": accuracy}
    return Signal(id=id_, kind="location_eta", source="phone" if mode == "live" else "fixture", source_mode=mode,
                  observed_at=observed, route_duration_seconds=journey_min * 60, position=position)


def flight(landing=datetime(2026, 10, 24, 15, 45, tzinfo=UTC), journey_min=35, id_="flight"):
    return Signal(id=id_, kind="flight_delay", source="fixture", source_mode="mock",
                  observed_at=datetime(2026, 10, 24, 12, tzinfo=UTC), estimated_arrival_at=landing,
                  route_duration_seconds=None if journey_min is None else journey_min * 60)


MEETING = event("meeting", "Project meeting", datetime(2026, 10, 24, 16, tzinfo=UTC), 60, "UTC",
                destination="Downtown office", contacts=("alex",))


# ---------- section 14: dinner, 18:40, 60-minute journey ----------

def test_dinner_forty_minutes_late_sends_an_eta_not_a_new_time():
    impacts, plans = process(location(), [event()], [SAM], now=datetime(2026, 10, 25, 1, 40, tzinfo=UTC))
    impact, action = impacts[0], plans[0].actions[0]
    assert impact.earliest_arrival_at == datetime(2026, 10, 25, 2, 40, tzinfo=UTC)  # 7:40 PM PDT
    assert impact.late_by_seconds == 40 * 60
    assert impact.reason == ("A 60 minute journey at 6:40 PM reaches Dinner with Sam at 7:40 PM, "
                             "40 minutes after it starts at 7:00 PM")
    assert action.message_text == "Hey Sam, I'm running about 40 minutes late. ETA around 7:40 PM—sorry!"
    assert action.new_start_at is None and action.contact_id == "sam"
    snapshot = plans[0].evidence_snapshot
    assert snapshot["proposal_kind"] == "running_late"
    assert snapshot["candidate_times"][0]["start_at"] == "2026-10-25T02:45:00+00:00"  # still offered as an option


# ---------- section 14: flight 15:45 + 30 exit + 35 journey ----------

def test_flight_reaches_meeting_at_four_fifty_and_proposes_a_new_time():
    impacts, plans = process(flight(), [MEETING], [ALEX], now=datetime(2026, 10, 24, 12, tzinfo=UTC))
    impact, action = impacts[0], plans[0].actions[0]
    assert impact.earliest_arrival_at == datetime(2026, 10, 24, 16, 50, tzinfo=UTC)
    assert impact.late_by_seconds == 50 * 60
    assert impact.evidence_times["estimated_landing_at"] == datetime(2026, 10, 24, 15, 45, tzinfo=UTC)
    assert "30 minute airport exit and a 35 minute journey" in impact.reason
    assert action.new_start_at == datetime(2026, 10, 24, 17, tzinfo=UTC)  # 16:50 rounded up, still free
    assert action.new_end_at == datetime(2026, 10, 24, 18, tzinfo=UTC)  # same one-hour length
    assert action.message_text == ("Hi Alex, my flight is delayed, and I'll be about 50 minutes late for "
                                   "Project meeting. Would 5:00 PM work instead?")
    assert impact.confidence == "high"


def test_same_flight_leaves_a_later_feasible_event_alone():
    later = event("later", "Later meeting", datetime(2026, 10, 24, 18, tzinfo=UTC), 60, "UTC", destination="Office")
    impacts, _ = process(flight(), [later, MEETING], [ALEX])
    assert [i.event_id for i in impacts] == ["meeting"]


def test_flight_without_estimate_is_unknown_not_on_time():
    impacts, plans = process(flight(landing=None), [MEETING], [ALEX])
    assert impacts == [] and plans == []


def test_flight_without_journey_time_is_medium_confidence():
    impacts, plans = process(flight(journey_min=None), [MEETING], [ALEX])
    assert impacts[0].earliest_arrival_at == datetime(2026, 10, 24, 16, 15, tzinfo=UTC)  # landing + exit only
    assert impacts[0].confidence == "medium"
    assert "journey time unknown" in plans[0].evidence_snapshot["confidence_reasons"][1]


# ---------- section 14: remote and uncertain events ----------

def test_remote_event_is_never_moved_for_distance():
    remote = event(mode="remote", destination=None)
    assert process(location(), [remote], [SAM]) == ([], [])


def test_unknown_mode_asks_for_clarification_instead_of_acting():
    unknown = event(mode="unknown")
    impacts, plans = process(location(), [unknown], [SAM])
    assert impacts[0].status == "needs_clarification" and impacts[0].confidence == "low"
    assert "Confirm whether it's in person" in impacts[0].reason
    assert plans == []


def test_missing_destination_asks_for_clarification():
    impacts, plans = process(location(), [event(destination=None)], [SAM])
    assert impacts[0].status == "needs_clarification" and "Confirm where it is" in impacts[0].reason
    assert plans == []


# ---------- section 14: stale or uncertain phone location ----------

def test_stale_live_location_takes_no_action():
    now = datetime(2026, 10, 25, 1, 40, tzinfo=UTC) + timedelta(minutes=3)
    assert process(location(mode="live", accuracy=20), [event()], [SAM], now=now) == ([], [])


def test_inaccurate_live_location_takes_no_action():
    assert process(location(mode="live", accuracy=500), [event()], [SAM]) == ([], [])


def test_rough_live_location_is_medium_confidence():
    now = datetime(2026, 10, 25, 1, 40, 30, tzinfo=UTC)
    impacts, _ = process(location(mode="live", accuracy=120), [event()], [SAM], now=now)
    assert impacts[0].confidence == "medium"


# ---------- thresholds ----------

def test_under_ten_minutes_late_is_not_an_impact():
    assert process(location(journey_min=29), [event()], [SAM]) == ([], [])  # 9 minutes late


def test_events_beyond_24_hours_are_ignored():
    tomorrow = event(start=datetime(2026, 10, 26, 2, 30, tzinfo=UTC))
    assert process(location(journey_min=26 * 60), [tomorrow], [SAM]) == ([], [])


# ---------- candidate times ----------

def test_three_candidates_skip_conflicts_and_keep_the_duration():
    dinner = event()
    busy = event("busy", "Busy", datetime(2026, 10, 25, 2, 30, tzinfo=UTC), 90, contacts=(), destination="Home")
    slots = candidate_times(dinner, [dinner, busy], datetime(2026, 10, 25, 2, 40, tzinfo=UTC))
    starts = [s for s, _ in slots]
    assert starts == [datetime(2026, 10, 25, 4, tzinfo=UTC), datetime(2026, 10, 25, 4, 15, tzinfo=UTC),
                      datetime(2026, 10, 25, 4, 30, tzinfo=UTC)]
    assert all(e - s == timedelta(minutes=90) for s, e in slots)


def test_no_slot_in_the_working_window_sends_a_message_without_a_time():
    late_dinner = event(start=datetime(2026, 10, 25, 4, 30, tzinfo=UTC))  # 9:30 PM PDT
    signal = location(observed=datetime(2026, 10, 25, 4, tzinfo=UTC), journey_min=90)  # arrives 10:30 PM
    _, plans = process(signal, [late_dinner], [SAM])
    action = plans[0].actions[0]
    assert action.new_start_at is None
    assert action.message_text.endswith("Could we find another time?")
    assert plans[0].evidence_snapshot["proposal_kind"] == "reschedule_no_slot"


def test_rounding_uses_the_events_local_clock():
    # India is UTC+5:30: 10:07 local rounds to 10:15 local, which is 04:45 UTC.
    assert round_up(datetime(2026, 10, 24, 4, 37, tzinfo=UTC), "Asia/Kolkata") == datetime(2026, 10, 24, 4, 45, tzinfo=UTC)


def test_overnight_event_uses_local_date_for_the_window():
    # 11:00 PM PDT dinner on Oct 24 = 06:00 UTC Oct 25; arrival after 10 PM local leaves no slot.
    late = event(start=datetime(2026, 10, 25, 6, tzinfo=UTC), minutes=60)
    slots = candidate_times(late, [late], datetime(2026, 10, 25, 6, 30, tzinfo=UTC))
    assert slots == []


# ---------- recipients and tone ----------

def test_unmapped_contact_leaves_the_draft_without_a_recipient():
    _, plans = process(location(), [event(contacts=())], [SAM])
    action = plans[0].actions[0]
    assert action.contact_id is None and action.message_text.startswith("I'm running about 40 minutes late")
    assert plans[0].evidence_snapshot["recipient_verified"] is False


def test_professional_tone_for_colleagues():
    meeting = event(contacts=("alex",), title="Design review")
    _, plans = process(location(), [meeting], [ALEX])
    assert plans[0].actions[0].message_text == (
        "Hi Alex, I'm running about 40 minutes late and expect to arrive around 7:40 PM. Apologies for the delay.")


def test_ids_stay_stable_for_the_api():
    impacts, plans = process(location(id_="signal_1"), [event()], [SAM])
    assert impacts[0].id == "impact_signal_1" and plans[0].id == "plan_signal_1"
    assert plans[0].actions[0].id == "action_signal_1_message_v1"
