"""Live listener: times heard on a call are checked against the calendar in real time."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from headsup.agents.listener import find_times, resolve
from headsup.models import Signal
from tests.conftest import kinds, wait_for

LA = ZoneInfo("America/Los_Angeles")
EVENING = datetime(2026, 10, 24, 19, 40, tzinfo=LA)  # the foundation fixture's dinner is 7:00-8:30 pm


@pytest.mark.parametrize("text, expected", [
    ("let's do dinner at 8", ["Sat 20:00"]),
    ("meet at 8:30 pm tomorrow", ["Sun 20:30"]),
    ("see you around eight thirty", ["Sat 20:30"]),
    ("tomorrow at 9", ["Sun 09:00"]),
    ("tonight at 7", ["Sat 19:00"]),
    ("call me at noon", ["Sat 12:00"]),
    ("the train leaves 9.15am", ["Sat 09:15"]),
    ("we said 20:00 right", ["Sat 20:00"]),
    ("at 8 or at 9?", ["Sat 20:00", "Sat 21:00"]),
    ("I'm 40 minutes away", []),
    ("table for 2 people", []),
    ("wait for 10 minutes", []),
])
def test_times_are_read_like_a_person_would(text, expected):
    assert [resolve(m, EVENING).strftime("%a %H:%M") for m in find_times(text)] == expected


def located(rt, at: datetime, minutes_away: int, mode: str = "mock") -> None:
    rt.foundation.ingest_signal(Signal(
        id=f"loc_{at.timestamp()}", kind="location_eta", source="test", source_mode=mode,
        observed_at=at.astimezone(timezone.utc), route_duration_seconds=minutes_away * 60))


def spoken_lines(rt) -> list[str]:
    return [e["data"]["text"] for e in rt.log.recent(1000)
            if e["kind"] == "device.out" and e["data"].get("type") == "speak"]


async def test_friend_says_8_at_740_and_you_are_40_minutes_away(rt):
    located(rt, EVENING, minutes_away=40)
    checks = await rt.listener.on_line("vibhu", "friend", "Let's just meet at 8 then")

    [check] = checks
    assert check.at == datetime(2026, 10, 24, 20, 0, tzinfo=LA)
    assert check.late_by == timedelta(minutes=20)
    [line] = spoken_lines(rt)
    assert "clashes with Dinner with Sam, 7 PM to 8:30 PM" in line
    assert "you'd get there around 8:20 PM, about 20 minutes late" in line


async def test_a_time_that_works_is_not_whispered(rt):
    located(rt, EVENING, minutes_away=10)
    [check] = await rt.listener.on_line("vibhu", "friend", "how about 9 pm")
    assert not check.problem
    assert spoken_lines(rt) == []
    assert "listen.check" in kinds(rt)  # still shown on the screen


async def test_without_a_fresh_location_only_clashes_are_checked(rt):
    located(rt, datetime.now(timezone.utc) - timedelta(minutes=10), minutes_away=90, mode="live")
    [check] = await rt.listener.on_line("vibhu", "friend", "tomorrow at 9 am works")
    assert check.late_by is None and check.away is None


async def test_the_same_warning_is_not_repeated_in_a_call(rt):
    located(rt, EVENING, minutes_away=40)
    await rt.listener.on_line("vibhu", "friend", "meet at 8")
    await rt.listener.on_line("vibhu", "vibhu", "8? ok")
    assert len(spoken_lines(rt)) == 1


async def test_live_transcript_lines_reach_the_listener(rt):
    located(rt, EVENING, minutes_away=40)
    rt.bus.publish("call.transcript", {"speaker": "friend", "text": "dinner at 8 tonight?"},
                   source="telephony", user="vibhu")
    event = await wait_for(rt, "device.out", type="speak")
    assert event["data"]["text"].startswith("Heads up: 8 PM clashes")
