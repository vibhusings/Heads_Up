"""Doctor: diagnoses what the Supervisor flags and repairs it, cheapest fix first.

Fixes that work are saved to a playbook, so the same failure is repaired
instantly next time without asking a model.
"""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel

from .agents.base import Agent
from .contracts import TaskState
from .supervisor import Alert
from .world import lower_first

# Repair ladders, cheapest first. Blocked tasks use the ladder for their reason.
LADDERS: dict[str, list[str]] = {
    "blocked:restaurant_no_answer": ["retry", "replan", "escalate", "safe_stop"],
    "blocked:booking_failed": ["retry", "escalate", "safe_stop"],
    "blocked:verify_failed": ["retry", "replan", "escalate", "safe_stop"],
    "blocked:no_agreement": ["escalate", "safe_stop"],
    "blocked:user_unreachable": ["safe_stop"],
    "blocked:dial_failed": ["retry", "escalate", "safe_stop"],
    "blocked:ivr_stuck": ["replan", "escalate", "safe_stop"],
    "blocked:line_silent": ["escalate", "safe_stop"],
    "blocked:bridge_failed": ["retry", "escalate", "safe_stop"],
    "blocked:hold_too_long": ["safe_stop"],
    "stuck": ["retry", "replan", "escalate", "safe_stop"],
    "loop": ["freeze_and_replan", "escalate", "safe_stop"],
    "task_overspend": ["degrade", "escalate", "safe_stop"],
    "deadline": ["escalate", "safe_stop"],
    "source_error": ["switch_source", "mark_stale"],
    "stale_data": ["switch_source", "mark_stale"],
    "overspend": ["degrade"],
    "model_errors": ["degrade"],
    "agent_down": ["restart_agent"],
}

# What "replan" switches between, per kind of task.
STRATEGIES: dict[str, list[str]] = {
    "plan_change": ["default", "online"],  # phone the restaurant, or book online
    "book_alternative": ["default", "online"],
    "hold_line": ["default", "operator"],  # follow the menu, or press 0 for a person
}

# Only real fixes are remembered; giving up is not a lesson.
NOT_LEARNED = {"escalate", "safe_stop", "retry"}


class Diagnosis(BaseModel):
    summary: str  # one sentence: what went wrong
    repair: str  # one of the candidate repairs


DOCTOR_SYSTEM = (
    "You are the Doctor in a self-repairing multi-agent system. Given a problem the Supervisor flagged, the "
    "recent events and a list of candidate repairs ordered cheapest first, pick the cheapest repair likely to "
    "work. Answer with one of the candidates exactly."
)


class Doctor(Agent):
    name = "doctor"

    def __init__(self, rt):
        super().__init__(rt)
        self.pending: dict[str, tuple[str, str]] = {}  # task id or "watch" -> (signature, repair)

    # ---- learning ----

    async def run(self) -> None:
        sub = self.rt.bus.subscribe("task.done", "watch.recovered")
        while True:
            e = await self.next(sub)
            key = e.task_id if e.kind == "task.done" else "watch"
            if key in self.pending:
                signature, repair = self.pending.pop(key)
                if repair not in NOT_LEARNED:
                    self.rt.playbook_save(signature, repair)
                    self.rt.bus.publish("doctor.learned", {"signature": signature, "repair": repair}, source=self.name)

    # ---- diagnosis ----

    def ladder(self, alert: Alert) -> list[str]:
        if alert.problem == "blocked":
            return LADDERS.get(f"blocked:{alert.detail}", ["escalate", "safe_stop"])
        return LADDERS.get(alert.problem, ["escalate", "safe_stop"])

    async def handle(self, alert: Alert) -> str:
        rt = self.rt
        task = rt.tasks.get(alert.task_id) if alert.task_id else None
        tried = task.repairs if task else []
        candidates = [r for r in self.ladder(alert) if r not in tried]
        if task and len(task.repairs) >= task.contract.max_repairs:
            candidates = [r for r in ("escalate", "safe_stop") if r not in tried] or ["safe_stop"]
        if not candidates:
            candidates = ["safe_stop"] if task else ["mark_stale"]

        learned = rt.playbook_get(alert.signature)
        if learned in candidates:
            choice, summary = learned, "seen before"
            rt.bus.publish("doctor.playbook", {"signature": alert.signature, "repair": choice},
                           source=self.name, task_id=alert.task_id)
        elif len(candidates) == 1:
            choice, summary = candidates[0], "only option left"
        else:
            context = [e for e in rt.log.recent(40) if not alert.task_id or e["task_id"] == alert.task_id][-15:]
            diagnosis = await rt.brain.decide(
                Diagnosis, system=DOCTOR_SYSTEM, tier="main", task_id=alert.task_id,
                offline=lambda: Diagnosis(summary=f"{alert.problem} ({alert.detail})", repair=candidates[0]),
                prompt=f"Problem: {alert.problem} {alert.detail}\nCandidates: {candidates}\n"
                       f"Recent events: {[(e['kind'], e['data']) for e in context]}",
            )
            choice = diagnosis.repair if diagnosis.repair in candidates else candidates[0]
            summary = diagnosis.summary

        rt.bus.publish("doctor.repair", {"problem": alert.problem, "detail": alert.detail, "repair": choice,
                       "diagnosis": summary, "attempt": len(tried) + 1}, source=self.name, task_id=alert.task_id)
        if task:
            task.repairs.append(choice)
            self.pending[task.id] = (alert.signature, choice)
        elif alert.problem in {"source_error", "stale_data"}:
            self.pending["watch"] = (alert.signature, choice)
        await self.apply(choice, alert)
        rt.supervisor.resolve(alert)
        return choice

    # ---- repairs ----

    async def apply(self, repair: str, alert: Alert) -> None:
        rt = self.rt
        task = rt.tasks.get(alert.task_id) if alert.task_id else None
        runner = rt.runners.get(alert.task_id) if alert.task_id else None

        if repair == "retry" and runner:
            runner.wake.set()
        elif repair == "replan" and task and runner:
            self._next_strategy(task)
            runner.wake.set()
        elif repair == "freeze_and_replan" and task and runner:
            task.context["frozen"] = True
            asyncio.create_task(self._unfreeze(task, runner))
        elif repair == "escalate" and task and runner:
            reason = alert.detail.replace("_", " ") or alert.problem
            await rt.devices.haptic(task.user, "urgent")
            await rt.devices.say(task.user, f"I couldn't finish: {lower_first(task.contract.goal)}. "
                                            f"The problem was {reason}. It's over to you.")
            runner.finish(TaskState.ESCALATED, f"escalated: {reason}")
            runner.wake.set()
        elif repair == "safe_stop" and task and runner:
            runner.finish(TaskState.STOPPED, "stopped safely")
            rt.release_locks(task.id)
            runner.wake.set()
        elif repair == "degrade":
            rt.brain.degraded = True
            rt.flight_watcher.interval_s *= 3
            rt.bus.publish("system.degraded", {"model": rt.settings.model_fast,
                           "poll_s": rt.flight_watcher.interval_s}, source=self.name)
        elif repair == "switch_source":
            if not rt.flight_watcher.switch_source():
                await self.apply("mark_stale", alert)

        elif repair == "mark_stale":
            rt.flight_watcher.stale = True
            rt.bus.publish("watch.stale", {"note": "not acting on flight data until a feed recovers"},
                           source=self.name)
        elif repair == "restart_agent" and alert.agent:
            await rt.restart_agent(alert.agent)
            rt.supervisor.crashed.discard(alert.agent)

    async def _unfreeze(self, task, runner) -> None:
        deadline = time.time() + 5
        while task.context.get("blocked") != "frozen by supervisor" and time.time() < deadline:
            await asyncio.sleep(0.1)
        task.context["frozen"] = False
        task.context["no_auto_retry"] = True
        self._next_strategy(task)
        runner.wake.set()

    def _next_strategy(self, task) -> None:
        options = STRATEGIES.get(task.kind, ["default"])
        nxt = options[(options.index(task.strategy) + 1) % len(options)] if task.strategy in options else options[0]
        if nxt != task.strategy:
            task.strategy = nxt
            self.rt.bus.publish("doctor.replanned", {"strategy": nxt}, source=self.name, task_id=task.id)
