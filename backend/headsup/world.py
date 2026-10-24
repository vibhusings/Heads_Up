"""What Heads Up knows about its users: profiles, plans, memory, commitments.

Times of day are minutes since midnight. That keeps the demo arithmetic
obvious; swap in real datetimes once calendar sync lands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def hhmm(minutes: int) -> str:
    h, m = divmod(int(minutes) % (24 * 60), 60)
    suffix = "am" if h < 12 else "pm"
    return f"{(h % 12) or 12}:{m:02d}{suffix}"


def to_min(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


@dataclass
class PlanItem:
    kind: str  # "flight", "dinner", ...
    title: str
    start: int  # minutes since midnight
    place: str = ""
    people: list[str] = field(default_factory=list)  # user ids or plain names
    flight_no: str | None = None
    travel_min: int = 0  # time to get here from the previous item
    id: str = field(default_factory=lambda: "p" + uuid.uuid4().hex[:6])
    source: str = "manual"  # "manual" or "calendar" (synced from the phone, replaced on every sync)


@dataclass
class Profile:
    user_id: str
    name: str
    phone: str
    # Private constraints for negotiating shared plans. Never sent to other agents.
    soft_latest: int | None = None  # prefers to start by this time
    hard_latest: int | None = None  # cannot start after this time
    context: dict[str, Any] = field(default_factory=lambda: {"airpods_in": False, "in_meeting": False})
    standing_rules: set[str] = field(default_factory=set)
    approvals: dict[str, int] = field(default_factory=dict)  # action type -> times approved


@dataclass
class World:
    profiles: dict[str, Profile] = field(default_factory=dict)
    plans: dict[str, list[PlanItem]] = field(default_factory=dict)
    memory: dict[str, list[str]] = field(default_factory=dict)
    commitments: dict[str, list[str]] = field(default_factory=dict)
    # Businesses Heads Up can call and hold for: key -> (display name, phone number).
    businesses: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Calendar events synced from each user's phone (calendar.CalEvent), next 7 days.
    calendar: dict[str, list] = field(default_factory=dict)

    def business(self, name: str) -> tuple[str, str] | None:
        key = name.strip().lower()
        for k, (display, phone) in self.businesses.items():
            if key in {k, display.lower()} or key.startswith(k):
                return display, phone
        return None

    def add_profile(self, p: Profile) -> None:
        self.profiles[p.user_id] = p
        self.plans.setdefault(p.user_id, [])
        self.memory.setdefault(p.user_id, [])
        self.commitments.setdefault(p.user_id, [])

    def items(self, user: str, kind: str | None = None) -> list[PlanItem]:
        return [i for i in self.plans.get(user, []) if kind is None or i.kind == kind]

    def uses_heads_up(self, person: str) -> bool:
        return person in self.profiles


def demo_world() -> World:
    """Two users for the demo: the judge ("vibhu") and a teammate playing Sam."""
    w = World()
    w.add_profile(Profile("vibhu", "Vibhu", "+15550000001"))
    w.add_profile(Profile("sam", "Sam", "+15550000002", soft_latest=to_min("20:00"), hard_latest=to_min("20:30")))
    # Placeholder numbers: point these at a teammate's phone for the demo, never a real business by accident.
    w.businesses = {
        "united": ("United Airlines", "+15550000101"),
        "pharmacy": ("CVS Pharmacy", "+15550000102"),
        "comcast": ("Comcast", "+15550000103"),
    }
    w.plans["vibhu"] = [
        PlanItem("flight", "UA 1234 Chicago to DCA", to_min("17:30"), place="DCA", flight_no="UA1234"),
        PlanItem("dinner", "Dinner with Sam", to_min("19:00"), place="Little Serow", people=["sam"], travel_min=35),
    ]
    w.plans["sam"] = [
        PlanItem("dinner", "Dinner with Vibhu", to_min("19:00"), place="Little Serow", people=["vibhu"]),
    ]
    return w


def lower_first(text: str) -> str:
    """Sentence-case a title without lowercasing names: "Dinner with Sam" -> "dinner with Sam"."""
    return text[:1].lower() + text[1:]
