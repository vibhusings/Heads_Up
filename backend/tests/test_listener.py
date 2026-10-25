"""Live listener: times heard on a call are checked against the calendar in real time."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from headsup.agents.listener import find_times, phone_events, resolve
from headsup.calendar_sync import parse_events
from headsup.models import Event, Signal
from headsup.notify import Notifier
from headsup.providers.calendar import MockCalendarProvider, UnavailableCalendarProvider
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


# ---------- the phone and live calendars, and the push alert ----------

def sunday_study_group() -> Event:
    return Event(id="google:study", provider="google", title="Study group", timezone="America/Los_Angeles",
                 start_at=datetime(2026, 10, 25, 9, 0, tzinfo=LA), end_at=datetime(2026, 10, 25, 10, 0, tzinfo=LA))


class CountingCalendar(MockCalendarProvider):
    calls = 0

    async def list_events(self, window):
        self.calls += 1
        return await super().list_events(window)


def ntfy(rt, sent: list[httpx.Request], status: int = 200) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rt.notifier = Notifier(replace(rt.settings, ntfy_topic="headsup-test-7f3a"), rt.bus, client=client)


async def test_a_clash_is_pushed_to_the_phone_mid_call(rt):
    sent: list[httpx.Request] = []
    ntfy(rt, sent)
    located(rt, EVENING, minutes_away=10)
    await rt.listener.on_line("vibhu", "friend", "dinner at 8 tonight?")

    [push] = sent
    assert str(push.url) == "https://ntfy.sh/headsup-test-7f3a"
    assert push.headers["Title"] == "Heads up: 8 PM on the call" and push.headers["Priority"] == "high"
    assert push.content.decode() == "8 PM clashes with Dinner with Sam, 7 PM to 8:30 PM."
    await wait_for(rt, "notify.sent")


async def test_without_a_topic_nothing_is_pushed_but_it_is_still_whispered(rt):
    located(rt, EVENING, minutes_away=10)
    await rt.listener.on_line("vibhu", "friend", "dinner at 8 tonight?")
    await wait_for(rt, "notify.skipped")
    assert len(spoken_lines(rt)) == 1


async def test_a_failed_push_does_not_stop_the_whisper(rt):
    sent: list[httpx.Request] = []
    ntfy(rt, sent, status=500)
    located(rt, EVENING, minutes_away=10)
    [check] = await rt.listener.on_line("vibhu", "friend", "dinner at 8 tonight?")
    assert check.problem and len(spoken_lines(rt)) == 1
    await wait_for(rt, "notify.failed")


async def test_events_synced_from_the_iphone_are_checked(rt):
    rt.world.calendar["vibhu"] = parse_events([{"id": "ek-1", "title": "Dentist", "date": "2026-10-25",
                                                "start": "09:30", "end": "10:30"}])
    located(rt, EVENING, minutes_away=10)
    [check] = await rt.listener.on_line("vibhu", "friend", "brunch tomorrow at 9 am?")
    assert [e.title for e in check.clashes] == ["Dentist"]
    assert "9 AM clashes with Dentist, 9:30 AM to 10:30 AM" in spoken_lines(rt)[0]


def test_phone_events_become_local_calendar_events():
    synced = parse_events([{"id": "a", "title": "Late show", "date": "2026-10-24", "start": "23:00", "end": "01:00"},
                           {"id": "b", "title": "Holiday", "date": "2026-10-24", "start": "00:00", "end": "23:59",
                            "all_day": True}])
    [event] = phone_events(synced, LA)
    assert event.id == "phone:a" and event.timezone == "America/Los_Angeles"
    assert (event.start_at, event.end_at) == (datetime(2026, 10, 24, 23, 0, tzinfo=LA),
                                              datetime(2026, 10, 25, 0, 0, tzinfo=LA))  # cut at midnight


async def test_the_live_google_calendar_is_checked_and_fetched_once(rt):
    rt.settings.calendar_mode = "live"
    rt.calendar = CountingCalendar([sunday_study_group()])
    located(rt, EVENING, minutes_away=10)
    [check] = await rt.listener.on_line("vibhu", "friend", "tomorrow at 9 am works")
    await rt.listener.on_line("vibhu", "vibhu", "or at 9:30 am")
    assert [e.title for e in check.clashes] == ["Study group"]
    assert rt.calendar.calls == 1  # cached between lines


async def test_the_live_calendar_is_fetched_when_the_call_starts(rt):
    rt.settings.calendar_mode = "live"
    rt.calendar = CountingCalendar([sunday_study_group()])
    rt.bus.publish("call.started", {"other": "Sam"}, source="telephony", user="vibhu")
    for _ in range(100):
        if rt.calendar.calls:
            break
        await asyncio.sleep(0.02)
    assert rt.calendar.calls == 1


async def test_an_unreachable_live_calendar_still_checks_the_rest(rt):
    rt.settings.calendar_mode = "live"
    rt.calendar = UnavailableCalendarProvider()
    located(rt, EVENING, minutes_away=10)
    [check] = await rt.listener.on_line("vibhu", "friend", "dinner at 8 tonight?")
    assert [e.title for e in check.clashes] == ["Dinner with Sam"]
    await wait_for(rt, "listen.calendar_unavailable", error_code="AUTH_REQUIRED")
