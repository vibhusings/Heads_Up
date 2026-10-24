"""Time of day for the agents. Real local time by default; the demo can set
it and speed it up so "leave now" and the evening receipt happen on stage."""

from __future__ import annotations

import time
from datetime import date, datetime


class Clock:
    def __init__(self):
        self._base: float | None = None  # minutes since midnight when last set
        self._set_at = 0.0
        self._day: date | None = None
        self.speed = 1.0

    def set(self, minutes: float, speed: float = 1.0, day: date | None = None) -> None:
        self._base = minutes
        self._set_at = time.time()
        self.speed = speed
        if day is not None:
            self._day = day

    def reset(self) -> None:
        self._base = None
        self._day = None
        self.speed = 1.0

    def now_min(self) -> float:
        if self._base is None:
            now = datetime.now()
            return now.hour * 60 + now.minute + now.second / 60
        return self._base + (time.time() - self._set_at) * self.speed / 60

    def today(self) -> date:
        return self._day or date.today()
