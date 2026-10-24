"""Task contracts: the rules every background task runs under.

The Supervisor enforces these from outside the agents, so no model output can
talk its way past a budget or a deadline.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    DONE = "done"
    FAILED = "failed"
    ESCALATED = "escalated"
    STOPPED = "stopped"


ACTIVE_STATES = {TaskState.RUNNING, TaskState.WAITING_USER}

# Actions that always need the user's explicit yes, whatever the contract says.
ALWAYS_ASK = {"spend_money", "share_personal_info", "delete", "message_new_contact"}


@dataclass
class TaskContract:
    goal: str
    allowed_tools: list[str]
    budget_usd: float
    deadline_s: float
    milestones: list[str]
    stall_s: float = 45.0
    max_repairs: int = 3
    needs_approval: list[str] = field(default_factory=list)


@dataclass
class Task:
    user: str
    contract: TaskContract
    kind: str
    context: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: "t" + uuid.uuid4().hex[:6])
    state: TaskState = TaskState.RUNNING
    created: float = field(default_factory=time.time)
    last_progress: float = field(default_factory=time.time)
    reached: list[str] = field(default_factory=list)
    spent_usd: float = 0.0
    repairs: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)  # action signatures, for loop detection
    strategy: str = "default"  # the Doctor changes this when it replans

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def progress(self, milestone: str) -> None:
        if milestone not in self.reached:
            self.reached.append(milestone)
        self.last_progress = time.time()

    def record_action(self, tool: str, args: dict[str, Any]) -> str:
        sig = action_signature(tool, args)
        self.actions.append(sig)
        return sig

    def check_tool(self, tool: str) -> None:
        if tool not in self.contract.allowed_tools:
            raise PermissionError(f"tool {tool!r} is not in the contract for task {self.id}")


def action_signature(tool: str, args: dict[str, Any]) -> str:
    blob = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return tool + ":" + hashlib.sha1(blob.encode()).hexdigest()[:10]
