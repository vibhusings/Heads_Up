"""Wearable bridge: plans to the AirPods and Apple Watch, decisions back.

When the engine creates a plan (or a plan is revised), the bridge asks the
user on the phone: two taps on the watch, the proposal whispered in the
AirPods, then a nod, a shake, a spoken yes/no, or a watch-screen tap. The
answer goes through the same LifecycleService.approve/reject as the
interface's buttons, for the exact plan version that was asked about.

It reuses the phone's existing protocol (see ios/Shared/Protocol.swift):
out: {"type": "haptic"}, {"type": "speak", "expect": "yes_no", "task_id"},
{"type": "approve", "task_id"} on the watch; in: {"type": "answer",
"task_id", "value", "via"}. The task_id names the plan and version:
"plan:<plan_id>:v<version>".

Rules: no answer means no action; a stale version is refused and the new
version is offered; wearables approve or reject, never edit.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .channels import pick_channel
from .lifecycle import LifecycleError
from .models import ExecutionOutcome, Plan

if TYPE_CHECKING:
    from .runtime import Runtime

OK_RESULTS = {"succeeded", "simulated"}
CHANNEL_BY_VIA = {"nod": "nod", "shake": "nod", "voice": "voice", "double_tap": "watch", "long_press": "watch"}


def ask_id(plan: Plan) -> str:
    return f"plan:{plan.id}:v{plan.version}"


class WearableBridge:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._asks: dict[str, asyncio.Task] = {}  # plan id -> the running ask

    @property
    def user(self) -> str:
        return self.rt.settings.wearable_user

    # ---------- offering plans ----------

    def offer(self, plan_ids: list[str]) -> None:
        """Ask about each plan on the wearables. A newer offer for the same plan replaces the old one."""
        for plan_id in plan_ids:
            self.cancel(plan_id)
            self._asks[plan_id] = asyncio.create_task(self._ask(plan_id), name=f"wearable-{plan_id}")

    def cancel(self, plan_id: str) -> None:
        """Stop asking, e.g. because the plan was approved with a button."""
        task = self._asks.pop(plan_id, None)
        if task and not task.done():
            task.cancel()

    def pending(self) -> Plan | None:
        """The newest plan still waiting for a decision."""
        plans = [p for p in self.rt.foundation.session().plans if p.status == "proposed"]
        return max(plans, key=lambda p: p.created_at, default=None)

    def describe(self, plan: Plan) -> tuple[str, bool]:
        """What to whisper, and whether there is anything the user can approve."""
        repo = self.rt.foundation
        impact = repo.get("impacts", plan.impact_id)
        event = repo.get("app_events", impact.event_id) if impact else None
        action = next((a for a in plan.actions if a.type == "send_message"), None)
        contact = repo.get("contacts", action.contact_id) if action and action.contact_id else None
        late = f"You'll be about {round(impact.late_by_seconds / 60)} minutes late to {event.title}. " \
            if impact and event else ""
        if action is None:
            return late + "There's nothing to send.", False
        if contact is None:
            return (late + f"I drafted: {action.message_text} But there's no verified contact, "
                    "so I can't send it."), False
        return late + f"Send {contact.display_name}: {action.message_text} Nod to send, or shake to skip.", True

    async def _ask(self, plan_id: str) -> None:
        bus, devices = self.rt.bus, self.rt.devices
        try:
            plan = self.rt.lifecycle.get_plan(plan_id)
        except LifecycleError:
            return
        if plan.status != "proposed":
            return
        spoken, actionable = self.describe(plan)
        if not actionable:
            await devices.haptic(self.user, "decision")
            await devices.say(self.user, spoken)
            bus.publish("wearable.blocked", {"plan_id": plan.id, "version": plan.version}, source="wearables",
                        user=self.user)
            return
        profile = self.rt.world.profiles.get(self.user)
        channel = pick_channel("decision", "normal", profile.context if profile else {})
        bus.publish("wearable.asked", {"plan_id": plan.id, "version": plan.version, "channel": channel.name.lower()},
                    source="wearables", user=self.user)
        answer = await devices.ask(self.user, ask_id(plan), spoken, channel)
        if self._asks.get(plan_id) is asyncio.current_task():
            del self._asks[plan_id]  # a newer offer for this plan keeps its own entry
        if answer["value"] == "none":
            bus.publish("wearable.no_answer", {"plan_id": plan.id, "version": plan.version},
                        source="wearables", user=self.user)
            return  # no answer, no action: the plan stays proposed for the interface or voice
        await self.decide(plan.id, plan.version, answer["value"], answer.get("via", "unknown"))

    # ---------- decisions ----------

    async def decide(self, plan_id: str, version: int, value: str, via: str) -> ExecutionOutcome | Plan | None:
        """Apply a yes/no from a wearable to exactly `version` of the plan."""
        bus, devices, lifecycle = self.rt.bus, self.rt.devices, self.rt.lifecycle
        channel = CHANNEL_BY_VIA.get(via, via)
        try:
            if value == "yes":
                outcome = lifecycle.approve(plan_id, version, self.user, channel)
            elif value == "no":
                rejected = lifecycle.reject(plan_id, version)
            else:
                return None
        except LifecycleError as err:
            await self._refused(plan_id, version, err)
            return None

        if value == "no":
            bus.publish("wearable.rejected", {"plan_id": plan_id, "version": version, "via": via},
                        source="wearables", user=self.user)
            await devices.haptic(self.user, "handled")
            await devices.say(self.user, "Okay, I won't send it.")
            return rejected

        ok = bool(outcome.results) and all(r.status in OK_RESULTS for r in outcome.results)
        simulated = any(r.status == "simulated" for r in outcome.results)
        bus.publish("wearable.approved", {"plan_id": plan_id, "version": version, "via": via,
                    "plan_status": outcome.plan.status, "results": [r.status for r in outcome.results]},
                    source="wearables", user=self.user)
        if ok:
            await devices.haptic(self.user, "handled")
            await devices.say(self.user, "Done. Sent in the demo outbox, not for real." if simulated else "Sent.")
        else:
            codes = ", ".join(sorted({r.error_code or r.status for r in outcome.results
                                      if r.status not in OK_RESULTS})) or "an unknown problem"
            await devices.haptic(self.user, "urgent")
            await devices.say(self.user, f"That didn't go through: {codes.replace('_', ' ').lower()}. "
                                         "Check the action history.")
        return outcome

    async def _refused(self, plan_id: str, version: int, err: LifecycleError) -> None:
        bus, devices = self.rt.bus, self.rt.devices
        bus.publish("wearable.refused", {"plan_id": plan_id, "version": version, "code": err.code},
                    source="wearables", user=self.user)
        try:
            current = self.rt.lifecycle.get_plan(plan_id)
        except LifecycleError:
            current = None
        if current is not None and current.status == "proposed" and current.version != version:
            await devices.say(self.user, "That proposal changed before you answered. Here's the new one.")
            self.offer([plan_id])
        elif err.code == "STALE_PLAN":
            await devices.say(self.user, "That proposal expired, so I didn't send anything.")
        else:
            await devices.say(self.user, "That proposal was already handled.")

    # ---------- voice commands ----------

    async def read_pending(self) -> None:
        plan = self.pending()
        if plan is None:
            await self.rt.devices.say(self.user, "Nothing needs you right now.")
            return
        self.offer([plan.id])

    async def decide_pending(self, value: str) -> None:
        plan = self.pending()
        if plan is None:
            await self.rt.devices.say(self.user, "Nothing needs you right now.")
            return
        self.cancel(plan.id)
        await self.decide(plan.id, plan.version, value, "voice")
