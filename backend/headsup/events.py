"""Event bus with a durable SQLite log.

Every agent talks through the bus, and every event is written to SQLite. The
log doubles as the audit trail, the checkpoint store and the feed behind the
judges' screen.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str  # dotted name, e.g. "watch.flight", "task.action"
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    user: str | None = None
    task_id: str | None = None
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class EventLog:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, ts REAL, kind TEXT, source TEXT,
                user TEXT, task_id TEXT, data TEXT
            );
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id);
            CREATE TABLE IF NOT EXISTS playbook (
                signature TEXT PRIMARY KEY, repair TEXT, wins INTEGER, updated REAL
            );
            """
        )

    def append(self, e: Event) -> None:
        self.db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            (e.id, e.ts, e.kind, e.source, e.user, e.task_id, json.dumps(e.data, default=str)),
        )
        self.db.commit()

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, ts, kind, source, user, task_id, data FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "source": r[3], "user": r[4], "task_id": r[5], "data": json.loads(r[6])}
            for r in reversed(rows)
        ]


class Subscription:
    def __init__(self, prefixes: tuple[str, ...]):
        self.prefixes = prefixes
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    def matches(self, kind: str) -> bool:
        return not self.prefixes or any(kind.startswith(p) for p in self.prefixes)

    async def get(self) -> Event:
        return await self.queue.get()


class EventBus:
    def __init__(self, log: EventLog):
        self.log = log
        self._subs: list[Subscription] = []

    def subscribe(self, *prefixes: str) -> Subscription:
        sub = Subscription(prefixes)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def publish(self, kind: str, data: dict[str, Any] | None = None, **meta: Any) -> Event:
        e = Event(kind=kind, data=data or {}, **meta)
        self.log.append(e)
        for sub in self._subs:
            if sub.matches(kind):
                sub.queue.put_nowait(e)
        return e
