"""Live listener: checks times mentioned on a call against the calendar while the call is going on.

Each transcribed line (`call.transcript`, from a call placed through HeadsUp) is
scanned for a time ("at 8", "8:30 pm", "tomorrow at 7", "eight thirty"). The
time is checked against calendar events and, when a fresh travel estimate is
known, against how long it takes to get there. Problems are whispered in the
AirPods right away; every check is also published for the screen. Nothing is
sent to anyone: this only informs the user.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..calendar_sync import find_day_word, resolve_day
from ..models import Event, Signal
from .base import Agent

LATE_THRESHOLD = timedelta(minutes=10)  # same rule as the engine
DEFAULT_LENGTH = timedelta(hours=1)  # a plan said on a call has no end time
LIVE_LOCATION_MAX_AGE = timedelta(minutes=2)  # older live locations are not trusted
REPEAT_QUIET_S = 15 * 60  # don't repeat the same warning within a call

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
MINUTE_WORDS = {"fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45, "o'clock": 0}
HOUR = r"(\d{1,2}|" + "|".join(WORDS) + r")"
MINUTE = r"(?:[:.](\d{2})|\s+(fifteen|thirty|forty[ -]five|o'clock))?"
MERIDIEM = r"\s*(a\.?m\.?|p\.?m\.?)?"
# A bare number only counts as a time after "at/around/by/for/until/from"; otherwise it
# needs minutes or am/pm, so "40 minutes" or "2 people" are not read as times.
NOT_A_TIME = (r"(?!\s*(?:people|persons|guests|of\b|more|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?"
              r"|dollars|bucks|percent|%))")
CUE = re.compile(r"\b(?:at|around|by|for|until|till|from|say)\s+" + HOUR + MINUTE + MERIDIEM + r"\b" + NOT_A_TIME,
                 re.IGNORECASE)
BARE = re.compile(r"\b" + HOUR + r"(?:[:.](\d{2})" + MERIDIEM + r"|()\s*(a\.?m\.?|p\.?m\.?))(?!\w)", re.IGNORECASE)
NAMED = re.compile(r"\b(noon|midday|midnight)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Mention:
    hour: int  # 0-23 once resolved; 1-12 when meridiem is None
    minute: int
    meridiem: str | None  # "am", "pm" or None when not said
    day_word: str  # "", "today", "tonight", "tomorrow", a weekday


def find_times(text: str) -> list[Mention]:
    """Every time of day mentioned in one line of speech."""
    day = find_day_word(text)
    found: list[tuple[int, Mention]] = []
    for m in CUE.finditer(text):
        found.append((m.start(), _mention(m.group(1), m.group(2) or m.group(3), m.group(4), day)))
    for m in BARE.finditer(text):
        if not any(start <= m.start() < start + 40 for start, _ in found):
            found.append((m.start(), _mention(m.group(1), m.group(2), m.group(3) or m.group(5), day)))
    for m in NAMED.finditer(text):
        word = m.group(1).lower()
        found.append((m.start(), Mention(0 if word == "midnight" else 12, 0, "pm" if word != "midnight" else "am",
                                         day)))
    mentions = [mention for _, mention in sorted(found, key=lambda item: item[0]) if mention is not None]
    return list(dict.fromkeys(mentions))


def _mention(hour_text: str, minute_text: str | None, meridiem: str | None, day: str) -> Mention | None:
    hour = WORDS.get(hour_text.lower()) if not hour_text.isdigit() else int(hour_text)
    if minute_text is None:
        minute = 0
    elif minute_text.isdigit():
        minute = int(minute_text)
    else:
        minute = MINUTE_WORDS.get(minute_text.lower().replace("-", " "), MINUTE_WORDS.get(minute_text.lower(), 0))
    if hour is None or minute > 59:
        return None
    mer = meridiem.lower().replace(".", "") if meridiem else None
    if hour > 23 or (mer and not 1 <= hour <= 12):
        return None
    if hour > 12:  # 24-hour clock
        return Mention(hour, minute, "pm", day)
    return Mention(hour, minute, mer, day)


def resolve(mention: Mention, now: datetime) -> datetime:
    """The moment meant, in `now`'s time zone. Without am/pm, guess like a person would."""
    day = resolve_day(mention.day_word, now.date())
    hour = mention.hour % 12
    if mention.meridiem == "pm" or mention.day_word == "tonight":
        hour += 12
    elif mention.meridiem is None:
        if mention.hour == 12:
            hour = 12
        elif day == now.date():
            # the next time that hour comes round today, e.g. "at 8" said at 7:40 pm is 8 pm
            if datetime.combine(day, datetime.min.time().replace(hour=hour, minute=mention.minute),
                                now.tzinfo) <= now:
                hour += 12
        elif mention.hour <= 7:
            hour += 12  # "tomorrow at 7" is evening; "tomorrow at 9" is morning
    return datetime.combine(day, datetime.min.time().replace(hour=hour, minute=mention.minute), now.tzinfo)


@dataclass
class Check:
    at: datetime
    clashes: list[Event]
    late_by: timedelta | None
    arrival: datetime | None
    away: timedelta | None

    @property
    def problem(self) -> bool:
        return bool(self.clashes) or self.late_by is not None


def check_time(at: datetime, events: list[Event], away: timedelta | None, now: datetime) -> Check:
    """Clashes with calendar events, and lateness when the travel time is known."""
    end = at + DEFAULT_LENGTH
    clashes = [e for e in events if e.start_at < end and e.end_at > at]
    arrival = now + away if away is not None and at.date() == now.date() else None
    late_by = arrival - at if arrival is not None and arrival - at >= LATE_THRESHOLD else None
    return Check(at=at, clashes=clashes, late_by=late_by, arrival=arrival, away=away if arrival else None)


def spoken(check: Check) -> str:
    when = _clock(check.at)
    parts = []
    for event in check.clashes[:2]:
        local = ZoneInfo(event.timezone)
        parts.append(f"{when} clashes with {event.title}, "
                     f"{_clock(event.start_at.astimezone(local))} to {_clock(event.end_at.astimezone(local))}.")
    if check.late_by is not None:
        parts.append(f"You're about {_minutes(check.away)} away, so you'd get there around "
                     f"{_clock(check.arrival)}, about {_minutes(check.late_by)} late.")
    return "Heads up: " + " ".join(parts)


def _clock(value: datetime) -> str:
    return value.strftime("%-I:%M %p").replace(":00 ", " ")


def _minutes(value: timedelta) -> str:
    return f"{round(value.total_seconds() / 60)} minutes"


class LiveListener(Agent):
    name = "listener"

    def __init__(self, rt):
        super().__init__(rt)
        self._said: dict[tuple[str, str], float] = {}  # (user, time) -> when we last warned

    async def run(self) -> None:
        sub = self.rt.bus.subscribe("call.transcript")
        while True:
            e = await self.next(sub)
            self.beat()
            await self.on_line(e.user or self.rt.settings.wearable_user, e.data.get("speaker", ""),
                               e.data.get("text", ""))

    async def on_line(self, user: str, speaker: str, text: str) -> list[Check]:
        mentions = find_times(text)
        if not mentions:
            return []
        state = self.rt.foundation.session()
        tz = ZoneInfo(self.rt.settings.user_timezone or (state.events[0].timezone if state.events else "UTC"))
        now, away = self._now_and_travel(state.signals, tz)
        checks = []
        for mention in mentions:
            check = check_time(resolve(mention, now), state.events, away, now)
            checks.append(check)
            self.rt.bus.publish("listen.check", {
                "heard": text, "speaker": speaker, "time": check.at.isoformat(),
                "clashes": [e.title for e in check.clashes],
                "late_by_min": round(check.late_by.total_seconds() / 60) if check.late_by else None,
                "travel_known": away is not None,
            }, source=self.name, user=user)
            key = (user, check.at.isoformat())
            if check.problem and time.time() - self._said.get(key, 0) > REPEAT_QUIET_S:
                self._said[key] = time.time()
                await self.rt.devices.say(user, spoken(check))
        return checks

    def _now_and_travel(self, signals: list[Signal], tz: ZoneInfo) -> tuple[datetime, timedelta | None]:
        """Current time and travel time. A demo (mock) location brings its own clock; a live
        one is used only while fresh, otherwise travel is unknown and only clashes are checked."""
        now = datetime.now(timezone.utc)
        located = [s for s in signals if s.route_duration_seconds is not None]
        if not located:
            return now.astimezone(tz), None
        latest = max(located, key=lambda s: s.observed_at)
        if latest.source_mode == "mock":
            now = latest.observed_at
        elif now - latest.observed_at > LIVE_LOCATION_MAX_AGE:
            return now.astimezone(tz), None
        return now.astimezone(tz), timedelta(seconds=latest.route_duration_seconds)


__all__ = ["LiveListener", "Mention", "check_time", "find_times", "resolve", "spoken"]
