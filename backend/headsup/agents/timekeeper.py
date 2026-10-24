"""Timekeeper: the day's rhythm. Sends the "leave now" pulses before each plan
and the end-of-day receipt in the user's ear."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from ..calendar_sync import current_meeting
from ..world import hhmm, lower_first, to_min
from .base import Agent

FIXES_NOT_COUNTED = {"escalate", "safe_stop", "retry"}  # a retry is a second try, not a fix


class Receipt(BaseModel):
    spoken: str  # under 60 words, read aloud


RECEIPT_SYSTEM = (
    "You are Heads Up, a voice assistant. Turn today's activity counts into a short spoken end-of-day "
    "summary: what you handled, what broke and got fixed, anything handed back to the user, and what you "
    "spent. Plain, warm, under 60 words, no lists."
)


def day_facts(events: list[dict], user: str) -> dict:
    """Counts for the receipt, straight from the event log."""
    task_users = {e["task_id"]: e["user"] for e in events if e["kind"] == "task.created"}
    mine = {t for t, u in task_users.items() if u == user}
    goals = {e["task_id"]: e["data"]["goal"] for e in events if e["kind"] == "task.created" and e["task_id"] in mine}
    done = [goals[e["task_id"]] for e in events if e["kind"] == "task.done" and e["task_id"] in mine
            and e["data"].get("outcome") == "completed"]
    handed_back = [goals[e["task_id"]] for e in events
                   if e["kind"] in {"task.escalated", "task.stopped"} and e["task_id"] in mine]
    fixes = [e for e in events if e["kind"] == "doctor.repair" and e["data"]["repair"] not in FIXES_NOT_COUNTED]
    spend = [e["data"]["total"] for e in events if e["kind"] == "spend"]
    return {
        "handled": done,
        "handed_back": handed_back,
        "fixed_by_itself": len(fixes),
        "blocked_injections": sum(1 for e in events if e["kind"] == "security.injection" and e["user"] == user),
        "quiet_decisions": sum(1 for e in events if e["kind"] == "triage.decision" and e["user"] == user
                               and e["data"].get("decision") == "quiet"),
        "spent_usd": spend[-1] if spend else 0.0,
    }


def format_receipt(f: dict) -> str:
    parts = []
    if f["handled"]:
        parts.append(f"Today I handled {len(f['handled'])}: " + "; ".join(lower_first(g) for g in f["handled"]) + ".")
    else:
        parts.append("Quiet day. Nothing needed you.")
    if f["fixed_by_itself"]:
        parts.append(f"{f['fixed_by_itself']} things broke along the way and I fixed them myself.")
    if f["handed_back"]:
        parts.append(f"I handed {len(f['handed_back'])} back to you.")
    if f["blocked_injections"]:
        parts.append("Someone asked for your details and I didn't share them.")
    cents = round(f["spent_usd"] * 100)
    parts.append(f"I spent {cents} cents." if cents else "I spent less than a cent.")
    return " ".join(parts)


class Timekeeper(Agent):
    name = "timekeeper"

    def __init__(self, rt):
        super().__init__(rt)
        self.sent_leave: set[tuple[str, str, int]] = set()  # (user, item id, start) so a moved plan gets a new nudge
        self.last_now: float | None = None
        self.meeting_state: dict[str, bool] = {}

    async def run(self) -> None:
        while True:
            self.beat()
            now = self.rt.clock.now_min()
            for user in self.rt.world.profiles:
                self.check_meeting(user, now)
                await self.check_leave(user, now)
            # Fire when the clock crosses receipt time, not on a server started late in the evening.
            if self.last_now is not None and self.last_now < to_min(self.rt.settings.receipt_at) <= now:
                for user in self.rt.world.profiles:
                    await self.send_receipt(user)
            self.last_now = now
            await asyncio.sleep(self.rt.settings.timekeeper_tick_s)

    def check_meeting(self, user: str, now: float) -> None:
        """Calendar says you're in a meeting: decisions arrive as taps with silent approval.
        Only transitions are written, so a manual switch holds until the calendar changes."""
        meeting = current_meeting(self.rt.world, user, self.rt.clock.today(), now)
        in_meeting = meeting is not None
        if self.meeting_state.get(user) == in_meeting:
            return
        self.meeting_state[user] = in_meeting
        self.rt.world.profiles[user].context["in_meeting"] = in_meeting
        self.rt.bus.publish("ctx.update", {"in_meeting": in_meeting, "from": "calendar",
                            "meeting": meeting.title if meeting else None}, source=self.name, user=user)

    def leave_at(self, item) -> int:
        return item.start - item.travel_min - self.rt.settings.leave_buffer_min

    async def check_leave(self, user: str, now: float) -> None:
        for item in self.rt.world.items(user):
            if item.kind == "flight" or not item.travel_min:
                continue
            key = (user, item.id, item.start)
            if key in self.sent_leave or not (self.leave_at(item) <= now < item.start):
                continue
            if any(t.active and t.context.get("item_id") == item.id for t in self.rt.tasks.values()):
                continue  # being rearranged right now; nudge once it settles
            self.sent_leave.add(key)
            line = f"Time to leave for {lower_first(item.title)} at {item.place}. It's {item.travel_min} minutes away, starting {hhmm(item.start)}."
            open_items = self.rt.world.commitments.get(user, [])
            if open_items:
                line += " Don't forget: " + "; ".join(open_items) + "."
            await self.rt.devices.haptic(user, "leave_now")
            await self.rt.devices.say(user, line)
            self.rt.bus.publish("timekeeper.leave_now", {"item": item.title, "leave_at": hhmm(self.leave_at(item)),
                                "start": hhmm(item.start)}, source=self.name, user=user)

    async def send_receipt(self, user: str) -> str:
        facts = day_facts(self.rt.log.recent(5000), user)
        receipt = await self.rt.brain.decide(
            Receipt, system=RECEIPT_SYSTEM, tier="fast", offline=lambda: Receipt(spoken=format_receipt(facts)),
            prompt=f"Today's activity: {facts}",
        )
        await self.rt.devices.haptic(user, "handled")
        await self.rt.devices.say(user, receipt.spoken)
        self.rt.bus.publish("timekeeper.receipt", facts | {"spoken": receipt.spoken}, source=self.name, user=user)
        return receipt.spoken
