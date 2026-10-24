"""Base class for long-running agents: a run loop plus heartbeats.

Agents beat while they work and while they wait for events. An agent that
crashes or hangs stops beating, and the Supervisor notices.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ..events import Event, Subscription

if TYPE_CHECKING:
    from ..runtime import Runtime


class Agent:
    name = "agent"

    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        raise NotImplementedError

    def beat(self) -> None:
        self.rt.heartbeats[self.name] = time.time()

    async def next(self, sub: Subscription) -> Event:
        while True:
            try:
                return await asyncio.wait_for(sub.get(), timeout=1.0)
            except asyncio.TimeoutError:
                self.beat()

    def start(self) -> None:
        self.beat()
        self._task = asyncio.create_task(self._main(), name=self.name)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _main(self) -> None:
        try:
            await self.run()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - any crash is reported, the Doctor restarts us
            self.rt.bus.publish("agent.crashed", {"agent": self.name, "error": repr(err)}, source=self.name)
