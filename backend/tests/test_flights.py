"""Flights: found on the calendar, polled within quota, never faked in live mode."""

import asyncio
from datetime import date

from conftest import wait_for
from headsup.calendar_sync import flight_from_title, parse_events, sync_flights
from headsup.config import Settings
from headsup.runtime import Runtime
from headsup.services import AviationStackSource, SourceError
from headsup.world import demo_world, to_min

THU = date(2026, 10, 22)


def test_flight_numbers_from_calendar_titles():
    assert flight_from_title("Flight UA 1234 ORD to DCA") == "UA1234"
    assert flight_from_title("United 777 to Denver") == "UA777"
    assert flight_from_title("Delta flight 42") == "DL42"
    assert flight_from_title("B6 401 BOS-DCA") == "B6401"
    assert flight_from_title("Flight to Chicago (AA 100)") == "AA100"
    for not_a_flight in ["Room B2 101 study session", "Q3 2026 planning", "CS 351 lecture", "Lunch with Sam"]:
        assert flight_from_title(not_a_flight) is None, not_a_flight


def test_calendar_flights_replace_earlier_synced_ones_and_keep_manual_plans():
    w = demo_world()
    events = parse_events([{"id": "e1", "title": "Flight UA 1234 ORD to DCA", "date": "2026-10-22",
                            "start": "15:00", "end": "17:30", "location": "ORD"},
                           {"id": "e2", "title": "Flight DL 9 tomorrow", "date": "2026-10-23",
                            "start": "09:00", "end": "11:00"}])
    flights = sync_flights(w, "vibhu", events, THU)
    assert [(f.flight_no, f.start, f.source) for f in flights] == [("UA1234", to_min("17:30"), "calendar")]
    sync_flights(w, "vibhu", [], THU)  # flight removed from the calendar
    assert [p.source for p in w.items("vibhu", "flight")] == ["manual"]  # the hand-entered demo flight stays


async def test_a_calendar_flight_is_watched_and_its_delay_moves_dinner(rt):
    rt.world.plans["vibhu"] = [p for p in rt.world.plans["vibhu"] if p.kind != "flight"]  # no hardcoded flight
    rt.clock.set(to_min("12:00"), day=THU)
    rt.devices.handle("vibhu", {"type": "calendar", "events": [
        {"id": "f1", "title": "United 777 Chicago to DCA", "date": "2026-10-22", "start": "15:00", "end": "17:30"}]})
    synced = await wait_for(rt, "calendar.synced")
    assert synced["data"]["flights"] == ["UA777"]
    rt.flight_delays["UA777"] = 90
    await wait_for(rt, "watch.flight", flight_no="UA777", delay_min=90)
    await wait_for(rt, "task.done", outcome="completed")


class CountingSource:
    name = "real"

    def __init__(self, min_interval_s, fail=False):
        self.min_interval_s = min_interval_s
        self.fail = fail
        self.calls = 0

    async def status(self, flight_no):
        self.calls += 1
        if self.fail:
            raise SourceError("real: HTTP 500")
        return {"flight_no": flight_no, "delay_min": 0}


async def test_real_source_is_polled_once_per_interval_and_only_near_landing(rt):
    src = CountingSource(min_interval_s=1000)
    rt.flight_watcher.sources = [src]
    rt.clock.set(to_min("10:00"), day=THU)  # demo flight lands 5:30pm: outside the 3-hour window
    await asyncio.sleep(0.5)
    assert src.calls == 0
    rt.clock.set(to_min("16:00"), day=THU)  # inside the window
    await asyncio.sleep(1.0)  # ten watcher ticks
    assert src.calls == 1


async def test_failing_real_source_goes_stale_instead_of_using_simulated_data(rt):
    rt.settings.flight_error_retry_s = 0.05
    rt.flight_watcher.sources = [CountingSource(min_interval_s=1000, fail=True)]
    rt.clock.set(to_min("16:00"), day=THU)
    await wait_for(rt, "supervisor.alert", problem="source_error")
    await wait_for(rt, "watch.stale")
    assert rt.flight_watcher.stale and rt.flight_watcher.source_name == "real"
    assert "watch.source_switched" not in [e["kind"] for e in rt.log.recent(500)]


def test_live_mode_uses_only_the_real_feed():
    rt = Runtime(Settings(offline=True, db_path=":memory:", aviationstack_key="test-key"))
    assert [type(s) for s in rt.flight_watcher.sources] == [AviationStackSource]
    assert rt.flight_watcher.sources[0].min_interval_s == 600
