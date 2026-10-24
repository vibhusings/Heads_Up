"""Triage, Planner and Judge: decide whether to act, and write the task contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..contracts import Task, TaskContract
from ..world import PlanItem, hhmm, lower_first, to_min
from .base import Agent


class TriageDecision(BaseModel):
    decision: Literal["quiet", "prepare", "act"]
    urgency: Literal["low", "normal", "critical"]
    reason: str


class PlanChangeDraft(BaseModel):
    spoken_question: str  # under 25 words, read aloud in the user's ear
    sms_text: str  # for people who do not use Heads Up


TRIAGE_SYSTEM = (
    "You are the triage agent of Heads Up, a voice assistant that stays quiet unless a decision is needed. "
    "Given a change in live data and the user's plans, choose: quiet (no effect on plans), prepare (might matter "
    "later), or act (a plan breaks unless something changes). Prefer quiet when unsure."
)

PLANNER_SYSTEM = (
    "You are the planning agent of Heads Up. Write one short spoken yes/no question for the user, and a friendly "
    "SMS the assistant could send on their behalf. Plain words, no emoji, mention the new time."
)


def new_start_for(item: PlanItem, eta: int) -> int:
    """Earliest start the user can make after landing, rounded up to a quarter hour."""
    earliest = eta + item.travel_min
    return -(-earliest // 15) * 15


class Triage(Agent):
    name = "triage"

    async def run(self) -> None:
        sub = self.rt.bus.subscribe("watch.flight")
        while True:
            e = await self.next(sub)
            self.beat()
            await self.on_flight_change(e.user, e.data)

    async def on_flight_change(self, user: str, data: dict) -> None:
        eta = data["eta"]
        affected = [i for i in self.rt.world.items(user) if i.kind != "flight" and i.start < eta + i.travel_min]

        def offline() -> TriageDecision:
            if affected:
                return TriageDecision(decision="act", urgency="normal",
                                      reason=f"Landing at {hhmm(eta)} misses {affected[0].title} at {hhmm(affected[0].start)}")
            return TriageDecision(decision="quiet", urgency="low", reason="No plans affected")

        plans = "; ".join(f"{i.title} at {hhmm(i.start)} ({i.travel_min} min away)" for i in self.rt.world.items(user))
        decision = await self.rt.brain.decide(
            TriageDecision, system=TRIAGE_SYSTEM, tier="fast", offline=offline,
            prompt=f"Flight {data['flight_no']} now delayed {data['delay_min']} min, landing {hhmm(eta)}. Plans: {plans}",
        )
        self.rt.bus.publish("triage.decision", decision.model_dump() | {"flight_no": data["flight_no"]},
                            source=self.name, user=user)
        if decision.decision == "act" and affected:
            await self.rt.planner.plan_change(user, affected[0], new_start_for(affected[0], eta), decision.urgency)


class Planner:
    """Turns a decision into a task with a contract. Called directly, not an event loop."""

    def __init__(self, rt):
        self.rt = rt

    def hold_line(self, user: str, business: str, phone: str, purpose: str) -> Task:
        s = self.rt.settings
        contract = TaskContract(
            goal=f"Get through to {business} for you: {purpose}",
            allowed_tools=["dial_business", "send_dtmf", "speak_to_business", "ask_user", "conference_user"],
            budget_usd=0.50,
            deadline_s=4 * 3600,
            milestones=["dialed", "through_menu", "human_on_line", "connected"],
            stall_s=s.hold_limit_s * 3 + 600,  # waiting on hold is not being stuck
        )
        task = Task(user=user, contract=contract, kind="hold_line", context={
            "business": business, "phone": phone, "purpose": purpose, "hold_limit_s": s.hold_limit_s,
            "urgency": "normal",
        })
        self.rt.spawn(task)
        return task

    async def plan_change(self, user: str, item: PlanItem, new_time: int, urgency: str) -> Task:
        if any(t.active and t.context.get("item_id") == item.id for t in self.rt.tasks.values()):
            return next(t for t in self.rt.tasks.values() if t.active and t.context.get("item_id") == item.id)
        name = self.rt.world.profiles[user].name
        others = [self.rt.world.profiles[p].name if p in self.rt.world.profiles else p for p in item.people]
        with_whom = " and ".join(others) or "your group"

        def offline() -> PlanChangeDraft:
            return PlanChangeDraft(
                spoken_question=f"You'll miss {lower_first(item.title)} at {hhmm(item.start)}. Move it to "
                                f"{hhmm(new_time)} and tell {with_whom}?",
                sms_text=f"Hi! {name}'s flight is delayed. Could we push dinner at {item.place} to {hhmm(new_time)}?",
            )

        draft = await self.rt.brain.decide(
            PlanChangeDraft, system=PLANNER_SYSTEM, tier="main", offline=offline,
            prompt=f"{name} will be late for '{item.title}' at {item.place}, {hhmm(item.start)}, with {with_whom}. "
                   f"Proposed new time {hhmm(new_time)}.",
        )
        contract = TaskContract(
            goal=f"Move {item.title} to about {hhmm(new_time)} and tell {with_whom}",
            allowed_tools=["ask_user", "negotiate", "sms", "phone_restaurant", "online_booking", "haptic"],
            budget_usd=self.rt.settings.default_task_budget_usd,
            deadline_s=600,
            milestones=["approved", "agreed", "booked", "notified"],
            needs_approval=["move_reservation"],
        )
        task = Task(user=user, contract=contract, kind="plan_change", context={
            "item_id": item.id, "place": item.place, "people": item.people, "proposed": new_time,
            "old_time": item.start, "question": draft.spoken_question, "sms": draft.sms_text,
            "urgency": urgency,
        })
        self.rt.spawn(task)
        return task


class JudgeVerdict(BaseModel):
    tell_user: bool
    spoken: str  # what to whisper, under 30 words
    offer_action: bool  # true when there is something Heads Up can do about it


JUDGE_SYSTEM = (
    "You are the judge agent of Heads Up. Given research findings about a plan the user just made, decide if "
    "anything is worth interrupting them for. Only tell them about problems or useful catches, never trivia."
)


class Judge:
    def __init__(self, rt):
        self.rt = rt

    async def review(self, user: str, plan: dict, finding: dict, clashes: list[str] | None = None) -> JudgeVerdict:
        clashes = clashes or []

        def offline() -> JudgeVerdict:
            parts: list[str] = []
            if clashes:
                parts.append(f"Heads up: that plan at {hhmm(to_min(plan['time']))} overlaps {' and '.join(clashes)}.")
            alt = None
            if not finding.get("is_open", True):
                alt = (finding.get("alternatives") or [None])[0]
                parts.append(f"{'Also, ' if clashes else 'Heads up: '}{plan['place']} is closed then. "
                             f"{finding.get('notes', '')}".strip())
                # Don't offer to book a time that already clashes with the calendar.
                if alt and not clashes:
                    parts.append(f"{alt} has a table. Want me to book it and tell everyone?")
            if not parts:
                return JudgeVerdict(tell_user=False, spoken="", offer_action=False)
            return JudgeVerdict(tell_user=True, spoken=" ".join(parts), offer_action=bool(alt) and not clashes)

        return await self.rt.brain.decide(
            JudgeVerdict, system=JUDGE_SYSTEM, tier="fast", offline=offline,
            prompt=f"Plan: {plan}. Findings: {finding}. Calendar conflicts: {clashes or 'none'}. "
                   "Only offer to book if there are no calendar conflicts.",
        )
