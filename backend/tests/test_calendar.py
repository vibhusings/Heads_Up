"""Calendar sync, meeting detection and conflict checks."""

import asyncio
from datetime import date

from conftest import wait_for
from headsup.calendar_sync import conflicts, parse_events, resolve_day
from headsup.telephony import CallSession
from headsup.world import demo_world, to_min

THU = date(2026, 10, 22)  # a Thursday


def event(id_, title, day, start, end, **extra):
    return {"id": id_, "title": title, "date": day, "start": start, "end": end, **extra}


def test_resolve_day():
    assert resolve_day("Friday", THU) == date(2026, 10, 23)
    assert resolve_day("tomorrow", THU) == date(2026, 10, 23)
    assert resolve_day("thursday", THU) == THU
    assert resolve_day("next thursday", THU) == date(2026, 10, 29)
    assert resolve_day("", THU) == THU


def test_parse_skips_malformed_events():
    events = parse_events([event("a", "Standup", "2026-10-22", "09:00", "09:15"), {"id": "b", "title": "broken"}])
    assert [e.title for e in events] == ["Standup"]


def test_conflict_window_includes_travel_buffer():
    w = demo_world()
    w.calendar["vibhu"] = parse_events([event("s", "Study group", "2026-10-23", "18:30", "20:00")])
    fri = date(2026, 10, 23)
    assert conflicts(w, "vibhu", fri, to_min("19:00"), THU) == ["your 6:30pm Study group"]
    assert conflicts(w, "vibhu", fri, to_min("21:00"), THU) == []  # starts 30 min after it ends: fine
    assert conflicts(w, "vibhu", date(2026, 10, 24), to_min("19:00"), THU) == []  # other day


async def test_meeting_on_calendar_switches_to_tap_only(rt):
    rt.clock.set(to_min("14:00"), day=THU)
    rt.devices.handle("vibhu", {"type": "calendar", "events": [event("m", "Design review", "2026-10-22", "13:30", "14:30")]})
    await wait_for(rt, "ctx.update", **{"in_meeting": True, "meeting": "Design review"})
    rt.flight_delays["UA1234"] = 90
    await wait_for(rt, "task.done", outcome="completed")
    ask = next(e["data"] for e in rt.log.recent(1000) if e["kind"] == "device.out" and e["data"].get("type") == "approve")
    assert "Move it to" in ask["text"]  # sent to the watch for silent approval, not spoken


async def test_meeting_ends_when_the_event_does(rt):
    rt.clock.set(to_min("14:29") + 0.5, speed=60, day=THU)  # the meeting ends 0.5s from now
    rt.devices.handle("vibhu", {"type": "calendar", "events": [event("m", "Design review", "2026-10-22", "13:30", "14:30")]})
    await wait_for(rt, "ctx.update", **{"in_meeting": True})
    for _ in range(60):
        if rt.world.profiles["vibhu"].context["in_meeting"] is False:
            break
        await asyncio.sleep(0.05)
    states = [e["data"]["in_meeting"] for e in rt.log.recent(500)
              if e["kind"] == "ctx.update" and e["data"].get("from") == "calendar" and e["user"] == "vibhu"]
    assert states[-2:] == [True, False]


async def test_call_plan_that_clashes_is_flagged_and_not_booked(rt):
    rt.clock.set(to_min("12:00"), day=THU)
    rt.devices.handle("vibhu", {"type": "calendar", "events": [
        event("s", "Study group", "2026-10-23", "18:30", "20:00", location="McKeldin Library")]})
    call = CallSession(rt.bus, "vibhu", "Sam")
    call.add_line("vibhu", "Let's do Thai Chef near Dupont, Friday at 7.")
    call.end()
    c = await wait_for(rt, "calendar.conflict")
    assert c["data"]["day"] == "2026-10-23" and c["data"]["with"] == ["your 6:30pm Study group"]
    verdict = (await wait_for(rt, "judge.verdict"))["data"]
    assert "overlaps your 6:30pm Study group" in verdict["spoken"] and "closed" in verdict["spoken"]
    assert verdict["offer_action"] is False  # never offers to book a clashing time
    assert not any(t.kind == "book_alternative" for t in rt.tasks.values())


async def test_plan_with_no_clash_still_books(rt):
    rt.clock.set(to_min("12:00"), day=THU)
    rt.devices.handle("vibhu", {"type": "calendar", "events": [event("s", "Study group", "2026-10-23", "13:00", "14:00")]})
    call = CallSession(rt.bus, "vibhu", "Sam")
    call.add_line("vibhu", "Let's do Thai Chef near Dupont, Friday at 7.")
    call.end()
    await wait_for(rt, "task.done", outcome="completed")
    assert "calendar.conflict" not in [e["kind"] for e in rt.log.recent(1000)]
