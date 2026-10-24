"""Calendar: events synced from the iPhone (read-only), meeting detection,
and conflict checks for plans made on calls.

The phone reads every calendar it has (iCloud, Google, Outlook) through
EventKit and sends the next 7 days as local dates and times. Only titles,
times and locations cross over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .world import PlanItem, World, hhmm, to_min

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULT_DURATION_MIN = 90  # how long a plan from a call is assumed to take
CONFLICT_BUFFER_MIN = 30  # travel and slack around it


@dataclass
class CalEvent:
    id: str
    title: str
    day: date
    start: int  # minutes since midnight
    end: int
    place: str = ""
    all_day: bool = False

    def describe(self) -> str:
        return f"your {hhmm(self.start)} {self.title}"


def parse_events(raw: list[dict]) -> list[CalEvent]:
    """Events from the phone: {"id", "title", "date": "YYYY-MM-DD", "start": "HH:MM",
    "end": "HH:MM", "location", "all_day"}. Malformed entries are skipped."""
    events: list[CalEvent] = []
    for e in raw:
        try:
            start, end = to_min(e["start"]), to_min(e["end"])
            events.append(CalEvent(id=str(e["id"]), title=str(e.get("title") or "Busy"),
                                   day=date.fromisoformat(e["date"]), start=start,
                                   end=end if end > start else 24 * 60,  # runs past midnight
                                   place=str(e.get("location") or ""), all_day=bool(e.get("all_day"))))
        except (KeyError, ValueError, TypeError):
            continue
    return events


# Airline names people (and airline apps) write in calendar titles, to IATA codes.
AIRLINES = {
    "united": "UA", "delta": "DL", "american": "AA", "southwest": "WN", "jetblue": "B6", "alaska": "AS",
    "spirit": "NK", "frontier": "F9", "air canada": "AC", "british airways": "BA", "lufthansa": "LH",
    "air france": "AF", "emirates": "EK", "air india": "AI",
}
FLIGHT_CODE = re.compile(r"\b([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{1,4})\b")


def flight_from_title(title: str) -> str | None:
    """'Flight UA 1234 to DCA', 'United 1234', 'B6 401 BOS-DCA' -> 'UA1234', 'UA1234', 'B6401'.
    Needs the word "flight", an airline name, or a flight code followed by an airport code,
    so "Room B2 101" or "Q3 2026 review" are not mistaken for flights."""
    lowered = title.lower()
    for name, code in AIRLINES.items():
        if m := re.search(rf"\b{name}\b(?: airlines?)?\s+(?:flight\s+)?(\d{{1,4}})\b", lowered):
            return f"{code}{m.group(1)}"
    m = FLIGHT_CODE.search(title)
    if m and "flight" in lowered:
        return f"{m.group(1)}{m.group(2)}"
    if m and m.start() == 0 and re.search(r"\b[A-Z]{3}\b", title[m.end():]):
        return f"{m.group(1)}{m.group(2)}"  # "B6 401 BOS-DCA": a code, then airports
    return None


def sync_flights(world: World, user: str, events: list[CalEvent], today: date) -> list[PlanItem]:
    """Today's flights on the calendar become flight plan items, so the watcher tracks them.
    The plan item's time is the landing time (the event's end). Flights synced earlier but no
    longer on the calendar are dropped; manual plans are left alone."""
    flights: list[PlanItem] = []
    for e in events:
        code = flight_from_title(e.title)
        if code is None or e.day != today or e.all_day or e.end >= 24 * 60:
            continue  # other days, or landing after midnight: not tracked yet
        flights.append(PlanItem("flight", e.title, e.end, place=e.place, flight_no=code,
                                id=f"cal-{e.id}", source="calendar"))
    plans = world.plans.setdefault(user, [])
    plans[:] = [p for p in plans if p.source != "calendar" or p.kind != "flight"] + flights
    return flights


def resolve_day(text: str, today: date) -> date:
    """'Friday', 'tomorrow', 'tonight', '2026-10-30' or '' -> a date. Unknown means today."""
    t = (text or "").strip().lower()
    if not t or t in {"today", "tonight", "this evening"}:
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    for i, name in enumerate(WEEKDAYS):
        if name in t:
            ahead = (i - today.weekday()) % 7
            if "next" in t and ahead == 0:
                ahead = 7
            return today + timedelta(days=ahead)
    return today


def find_day_word(text: str) -> str:
    m = re.search(r"\b(today|tonight|tomorrow|(?:next )?(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b", text,
                  re.IGNORECASE)
    return m.group(1).lower() if m else ""


def conflicts(world: World, user: str, day: date, start: int, today: date,
              duration: int = DEFAULT_DURATION_MIN, buffer: int = CONFLICT_BUFFER_MIN) -> list[str]:
    """Things already on the calendar (or today's plans) that a new plan at `start` would clash with."""
    lo, hi = start - buffer, start + duration
    found = [e.describe() for e in world.calendar.get(user, [])
             if e.day == day and not e.all_day and e.start < hi and e.end > lo]
    if day == today:
        found += [f"your {hhmm(p.start)} {p.title}" for p in world.items(user)
                  if p.kind != "flight" and lo < p.start < hi]
    return found


def current_meeting(world: World, user: str, today: date, now_min: float) -> CalEvent | None:
    for e in world.calendar.get(user, []):
        if e.day == today and not e.all_day and e.start <= now_min < e.end:
            return e
    return None
