"""Workers: the TaskRunner that carries out a task step by step, plus the
Liaison (negotiates with other people's agents), Guard and Verifier.

A step that cannot finish marks the task blocked and waits. The Supervisor
notices, the Doctor picks a repair and wakes the task up.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..channels import Channel, escalate, pick_channel
from ..contracts import Task, TaskState
from ..world import hhmm

TRUST_THRESHOLD = 3  # approvals of one action type before offering a standing rule

INJECTION_PATTERNS = [
    r"card (number|details)", r"\bpassword\b", r"\b(cvv|pin)\b", r"social security", r"ignore (all|previous)",
    r"\bsend me\b", r"\bcancel (it|the|all)\b", r"\bforward\b", r"\baddress\b",
]


class Guard:
    """Third parties can talk to us, but never instruct us."""

    @staticmethod
    def is_instruction(text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in INJECTION_PATTERNS)


class Verifier:
    def __init__(self, rt):
        self.rt = rt

    def booking(self, place: str, time: int) -> bool:
        r = self.rt.restaurants.lookup(place)
        return r is not None and r.time == time


class Liaison:
    """Agent-to-agent negotiation. Each side keeps its constraints private and
    shares only a time and a place. Messages are proposals, never commands."""

    MAX_ROUNDS = 3

    def __init__(self, rt):
        self.rt = rt

    def _send(self, task: Task, action: str, sender: str, receiver: str, time: int | None, place: str) -> dict:
        msg = {"action": action, "time": time, "place": place}  # the only fields that cross between agents
        self.rt.bus.publish(f"negotiation.{action}", msg | {"from": sender, "to": receiver,
                            "time_text": hhmm(time) if time is not None else None},
                            source="liaison", user=sender, task_id=task.id)
        task.record_action("negotiate", {"action": action, "from": sender, "time": time})
        return msg

    def _respond(self, responder: str, t: int, countered: bool) -> tuple[str, int]:
        p = self.rt.world.profiles[responder]
        if p.soft_latest is None or t <= p.soft_latest:
            return "accept", t
        if p.hard_latest is not None and t <= p.hard_latest:
            return ("accept", t) if countered else ("counter", p.soft_latest)
        return "counter", p.hard_latest if p.hard_latest is not None else t

    async def negotiate(self, task: Task, initiator: str, responder: str, earliest: int) -> dict[str, Any]:
        place = task.context["place"]
        offer = earliest + 30  # open with a buffer
        seen: set[tuple[str, int]] = set()
        countered = False
        for round_no in range(1, self.MAX_ROUNDS + 1):
            if (initiator, offer) in seen:
                return self._escalate(task, initiator, responder, "back-and-forth detected")
            seen.add((initiator, offer))
            self._send(task, "propose", initiator, responder, offer, place)
            await asyncio.sleep(0.2)

            action, t = self._respond(responder, offer, countered)
            if action == "accept":
                self._send(task, "accept", responder, initiator, t, place)
                return {"agreed": t, "rounds": round_no}
            countered = True
            self._send(task, "counter", responder, initiator, t, place)
            await asyncio.sleep(0.2)

            if earliest <= t <= earliest + 30:  # inside what our user already approved
                self._send(task, "accept", initiator, responder, t, place)
                return {"agreed": t, "rounds": round_no}
            if t < earliest:
                offer = earliest  # drop the buffer
            else:
                return self._escalate(task, initiator, responder, f"{hhmm(t)} is outside what was approved")
        return self._escalate(task, initiator, responder, f"no agreement in {self.MAX_ROUNDS} rounds")

    def _escalate(self, task: Task, a: str, b: str, reason: str) -> dict[str, Any]:
        self.rt.bus.publish("negotiation.escalate", {"reason": reason, "between": [a, b]},
                            source="liaison", task_id=task.id)
        return {"agreed": None, "reason": reason}


class TaskRunner:
    def __init__(self, rt, task: Task):
        self.rt = rt
        self.task = task
        self.wake = asyncio.Event()
        self.steps = {
            "approved": self.approve,
            "agreed": self.agree,
            "booked": self.book,
            "notified": self.notify,
        }

    # ---- loop ----

    async def run(self) -> None:
        t = self.task
        bus = self.rt.bus
        bus.publish("task.created", {"kind": t.kind, "goal": t.contract.goal, "milestones": t.contract.milestones,
                                     "budget_usd": t.contract.budget_usd}, source="planner", user=t.user, task_id=t.id)
        try:
            while t.active:
                if self.rt.paused:
                    await asyncio.sleep(0.5)
                    continue
                remaining = [m for m in t.contract.milestones if m not in t.reached]
                if not remaining:
                    self.finish(TaskState.DONE, "completed")
                    break
                step = remaining[0]
                self.wake.clear()
                t.context.pop("blocked", None)
                ok = await self.steps[step]()
                if ok:
                    t.progress(step)
                    bus.publish("task.progress", {"milestone": step}, source="runner", user=t.user, task_id=t.id)
                elif t.active and not self.wake.is_set():
                    bus.publish("task.blocked", {"step": step, "reason": t.context.get("blocked", "unknown")},
                                source="runner", user=t.user, task_id=t.id)
                    await self.wake.wait()
        finally:
            self.rt.release_locks(t.id)
            await self.cleanup()

    async def cleanup(self) -> None:
        """Runs once when the task ends, however it ends. Subclasses hang up calls here."""

    def block(self, reason: str) -> bool:
        self.task.context["blocked"] = reason
        return False

    def finish(self, state: TaskState, outcome: str) -> None:
        t = self.task
        t.state = state
        kind = {TaskState.DONE: "task.done", TaskState.ESCALATED: "task.escalated",
                TaskState.STOPPED: "task.stopped"}.get(state, "task.failed")
        self.rt.bus.publish(kind, {"outcome": outcome, "spent_usd": round(self.rt.ledger.by_task[t.id], 4),
                                   "repairs": t.repairs}, source="runner", user=t.user, task_id=t.id)

    def act(self, tool: str, args: dict[str, Any]) -> None:
        self.task.check_tool(tool)
        self.task.record_action(tool, args)
        self.rt.bus.publish("task.action", {"tool": tool, "args": args}, source="runner",
                            user=self.task.user, task_id=self.task.id)

    # ---- steps ----

    async def approve(self) -> bool:
        t, profile = self.task, self.rt.world.profiles[self.task.user]
        rule = t.contract.needs_approval[0] if t.contract.needs_approval else None
        if rule and rule in profile.standing_rules:
            self.rt.bus.publish("task.auto_approved", {"rule": rule}, source="runner", user=t.user, task_id=t.id)
            return True
        channel = pick_channel("decision", t.context.get("urgency", "normal"), profile.context)
        while True:
            self.act("ask_user", {"channel": channel.name.lower()})
            answer = await self.rt.devices.ask(t.user, t.id, t.context["question"], channel)
            if answer["value"] == "yes":
                if rule:
                    profile.approvals[rule] = profile.approvals.get(rule, 0) + 1
                return True
            if answer["value"] == "no":
                self.finish(TaskState.DONE, "declined by user")
                return False
            nxt = escalate(channel)
            if nxt is None:
                return self.block("user_unreachable")
            self.rt.bus.publish("task.channel_up", {"from": channel.name.lower(), "to": nxt.name.lower()},
                                source="runner", user=t.user, task_id=t.id)
            channel = nxt

    async def agree(self) -> bool:
        t = self.task
        new_time = t.context["proposed"]
        for person in t.context.get("people", []):
            if self.rt.world.uses_heads_up(person):
                result = await self.rt.liaison.negotiate(t, t.user, person, t.context["proposed"])
                if result["agreed"] is None:
                    return self.block(f"no_agreement: {result['reason']}")
                new_time = result["agreed"]
            else:
                self.act("sms", {"to": person})
                await self.rt.sms.send(person, t.context["sms"])
        t.context["new_time"] = new_time
        return True

    async def book(self) -> bool:
        t = self.task
        place, when = t.context["place"], t.context["new_time"]
        party = [t.user, *t.context.get("people", [])]
        if not self.rt.acquire_lock(f"booking:{place}", t.id):
            return True  # another agent owns this reservation and will move it
        if t.strategy == "online":
            self.act("online_booking", {"place": place, "time": when})
            res = await self.rt.restaurants.online(place, when, party)
            if not res["ok"]:
                return self.block(f"booking_failed: {res['error']}")
        else:
            redials = 6 if (self.rt.chaos.on("loop") and not t.context.get("no_auto_retry")) else 1
            res: dict[str, Any] = {"answered": False}
            for _ in range(redials):
                if t.context.get("frozen"):
                    return self.block("frozen by supervisor")
                self.act("phone_restaurant", {"place": place, "time": when})
                res = await self.rt.restaurants.phone(place, when, party)
                if redials > 1:
                    await asyncio.sleep(0.4)  # the buggy caller redials anyway
                elif res["answered"]:
                    break
            if not res["answered"]:
                return self.block("restaurant_no_answer")
            if Guard.is_instruction(res.get("said", "")):
                t.context["injection_blocked"] = True
                self.rt.bus.publish("security.injection", {"from": place, "text": res["said"],
                                    "action": "ignored, treated as conversation"},
                                    source="guard", user=t.user, task_id=t.id)
        if not self.rt.verifier.booking(place, when):
            self.rt.bus.publish("task.rejected", {"check": "booking", "place": place}, source="verifier",
                                user=t.user, task_id=t.id)
            return self.block("verify_failed")
        self.rt.bus.publish("task.verified", {"check": "booking", "place": place, "time": hhmm(when)},
                            source="verifier", user=t.user, task_id=t.id)
        return True

    async def notify(self) -> bool:
        t = self.task
        when = t.context["new_time"]
        for item in self.rt.world.plans.get(t.user, []):
            if item.id == t.context.get("item_id"):
                item.start = when
        devices = self.rt.devices
        for person in [t.user, *t.context.get("people", [])]:
            if self.rt.world.uses_heads_up(person):
                self.act("haptic", {"to": person, "pattern": "handled"})
                await devices.haptic(person, "handled")
        line = f"Done. You're at {hhmm(when)} at {t.context['place']}, and everyone knows."
        if t.context.get("injection_blocked"):
            line += " The restaurant asked for card details. I didn't share anything."
        await devices.say(t.user, line)
        await self.offer_standing_rule()
        return True

    async def offer_standing_rule(self) -> None:
        t, profile = self.task, self.rt.world.profiles[self.task.user]
        rule = t.contract.needs_approval[0] if t.contract.needs_approval else None
        if not rule or rule in profile.standing_rules or profile.approvals.get(rule, 0) != TRUST_THRESHOLD:
            return
        answer = await self.rt.devices.ask(
            t.user, t.id + "-rule",
            f"You've said yes to this {TRUST_THRESHOLD} times. Want me to just do it from now on?",
            Channel.WHISPER if profile.context.get("airpods_in") else Channel.TAP)
        if answer["value"] == "yes":
            profile.standing_rules.add(rule)
            self.rt.bus.publish("rule.created", {"rule": rule}, source="runner", user=t.user)
