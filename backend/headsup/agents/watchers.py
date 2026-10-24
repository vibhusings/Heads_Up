"""Watchers: poll live data and report only changes that matter. Plain code, no model."""

from __future__ import annotations

import asyncio
import time

from ..services import SourceError
from .base import Agent

MEANINGFUL_DELAY_CHANGE_MIN = 10
WATCH_BEFORE_ARRIVAL_MIN = 180  # real sources: start polling 3 hours before landing
WATCH_AFTER_ARRIVAL_MIN = 60  # ...and stop an hour after the (delayed) landing


class FlightWatcher(Agent):
    """Polls each flight in the users' plans.

    Simulated sources are cheap and polled every tick. Real sources cost quota,
    so each flight is polled at most every `min_interval_s`, and only while the
    flight is near landing.
    """

    name = "flight_watcher"

    def __init__(self, rt, sources, interval_s: float = 2.0):
        super().__init__(rt)
        self.sources = sources  # ordered: primary first, then backups
        self.active = 0
        self.interval_s = interval_s
        self.last_ok = time.time()
        self.stale = False
        self.erroring = False
        self.consecutive_errors = 0
        self.watching = False  # at least one flight is inside its polling window
        self.known_delay: dict[str, int] = {}
        self.last_attempt: dict[tuple[str, str], float] = {}  # (source, flight) -> time

    @property
    def source(self):
        return self.sources[self.active]

    @property
    def source_name(self) -> str:
        return self.source.name

    def switch_source(self) -> bool:
        if self.active + 1 >= len(self.sources):
            return False
        self.active += 1
        self.consecutive_errors = 0
        self.rt.bus.publish("watch.source_switched", {"to": self.source_name}, source=self.name)
        return True

    def overdue(self, now: float) -> bool:
        """No good data for much longer than the polling rhythm while a flight matters."""
        limit = max(self.rt.settings.stale_data_s, 3 * self.source.min_interval_s)
        return self.watching and not self.stale and now - self.last_ok > limit

    async def run(self) -> None:
        while True:
            self.beat()
            watching = False
            for user in self.rt.world.profiles:
                for item in self.rt.world.items(user, "flight"):
                    if self._in_window(item):
                        watching = True
                        await self._check(user, item)
            if watching and not self.watching:
                self.last_ok = time.time()  # the clock for staleness starts when watching does
            self.watching = watching
            await asyncio.sleep(self.interval_s)

    def _in_window(self, item) -> bool:
        if not self.source.min_interval_s:
            return True  # simulators: always
        now = self.rt.clock.now_min()
        landing = item.start + self.known_delay.get(item.flight_no, 0)
        return item.start - WATCH_BEFORE_ARRIVAL_MIN <= now <= landing + WATCH_AFTER_ARRIVAL_MIN

    def _due(self, flight_no: str) -> bool:
        gap = self.source.min_interval_s
        if not gap:
            return True
        if self.consecutive_errors:
            gap = min(gap, self.rt.settings.flight_error_retry_s)  # retry sooner after a failure
        return time.time() - self.last_attempt.get((self.source_name, flight_no), 0) >= gap

    async def _check(self, user: str, item) -> None:
        if not self._due(item.flight_no):
            return
        self.last_attempt[(self.source_name, item.flight_no)] = time.time()
        try:
            status = await self.source.status(item.flight_no)
        except SourceError as err:
            self.erroring = True
            self.consecutive_errors += 1
            self.rt.bus.publish("watch.error", {"source": self.source_name, "error": str(err)}, source=self.name)
            return
        self.consecutive_errors = 0
        if self.erroring or self.stale:
            self.erroring = self.stale = False
            self.rt.bus.publish("watch.recovered", {"source": self.source_name}, source=self.name)
        self.last_ok = time.time()

        delay = status["delay_min"]
        before = self.known_delay.get(item.flight_no, 0)
        if abs(delay - before) < MEANINGFUL_DELAY_CHANGE_MIN:
            return
        self.known_delay[item.flight_no] = delay
        self.rt.bus.publish(
            "watch.flight",
            {"flight_no": item.flight_no, "delay_min": delay, "was": before,
             "eta": item.start + delay, "item_id": item.id, "source": self.source_name},
            source=self.name, user=user,
        )
