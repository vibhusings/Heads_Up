"""Supervisor: plain code, no model. Watches every agent and task and hands
problems to the Doctor. It holds the kill switch, so nothing an agent says
can get past it."""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from .agents.base import Agent
from .contracts import TaskState


@dataclass
class Alert:
    problem: str
    detail: str = ""
    task_id: str | None = None
    agent: str | None = None
    ts: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.problem}:{self.task_id or self.agent or '-'}:{self.detail}"

    @property
    def signature(self) -> str:
        """What the playbook remembers: the kind of failure, not the instance."""
        return f"{self.problem}:{self.detail}"


class Supervisor(Agent):
    name = "supervisor"

    def __init__(self, rt):
        super().__init__(rt)
        self.open: set[str] = set()
        self.brain_errors: deque[float] = deque(maxlen=20)
        self._sub = rt.bus.subscribe("brain.error", "agent.crashed")
        self.crashed: set[str] = set()

    def resolve(self, alert: Alert) -> None:
        self.open.discard(alert.key)

    async def run(self) -> None:
        while True:
            self.beat()
            await self.tick()
            await asyncio.sleep(self.rt.settings.supervisor_tick_s)

    def _drain(self) -> None:
        while not self._sub.queue.empty():
            e = self._sub.queue.get_nowait()
            if e.kind == "brain.error":
                self.brain_errors.append(e.ts)
            elif e.kind == "agent.crashed":
                self.crashed.add(e.data["agent"])

    def check(self) -> list[Alert]:
        """Every rule in one place. Returns new problems only."""
        self._drain()
        s, rt, now = self.rt.settings, self.rt, time.time()
        found: list[Alert] = []

        for task in list(rt.tasks.values()):
            if not task.active:
                continue
            blocked = task.context.get("blocked")
            if blocked == "frozen by supervisor":
                pass  # the Doctor froze it on purpose and is already handling it
            elif blocked:
                found.append(Alert("blocked", blocked.split(":")[0], task_id=task.id))
            elif task.state == TaskState.RUNNING and now - task.last_progress > task.contract.stall_s:
                found.append(Alert("stuck", "no_progress", task_id=task.id))
            if now - task.created > task.contract.deadline_s:
                found.append(Alert("deadline", "", task_id=task.id))
            if task.actions:
                sig, n = Counter(task.actions).most_common(1)[0]
                seen = task.context.setdefault("loop_alerted", [])
                if n > s.loop_repeat_limit and sig not in seen:
                    seen.append(sig)
                    found.append(Alert("loop", sig.split(":")[0], task_id=task.id))
            if rt.ledger.by_task[task.id] > task.contract.budget_usd and not task.context.get("over_budget"):
                task.context["over_budget"] = True
                found.append(Alert("task_overspend", "", task_id=task.id))

        for agent, beat in rt.heartbeats.items():
            if agent != self.name and (agent in self.crashed or now - beat > s.heartbeat_timeout_s):
                found.append(Alert("agent_down", agent, agent=agent))

        watcher = rt.flight_watcher
        if watcher.consecutive_errors >= 2 and not watcher.stale:
            found.append(Alert("source_error", f"flight:{watcher.source_name}", agent=watcher.name))
        if watcher.overdue(now):
            found.append(Alert("stale_data", "flight", agent=watcher.name))

        if not rt.brain.degraded:
            if rt.ledger.projected_over():
                found.append(Alert("overspend", "daily"))
            if sum(1 for ts in self.brain_errors if now - ts < 60) >= 3:
                found.append(Alert("model_errors", ""))

        new = [a for a in found if a.key not in self.open]
        self.open.update(a.key for a in new)
        return new

    async def tick(self) -> None:
        for alert in self.check():
            self.rt.bus.publish("supervisor.alert", {"problem": alert.problem, "detail": alert.detail,
                                "agent": alert.agent}, source=self.name, task_id=alert.task_id)
            await self.rt.doctor.handle(alert)
