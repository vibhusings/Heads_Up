"""Device gateway: the only way agents reach a person.

Agents send intents (play a haptic pattern, say something, ask yes/no) and
get answers back. The iPhone app connects over a WebSocket; without one, the
judges' screen acts as a virtual device so the backend can be developed alone.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from fastapi import WebSocket

from .calendar_sync import parse_events, sync_flights
from .channels import Channel
from .events import EventBus
from .world import World

HAPTICS = {"handled", "decision", "leave_now", "urgent"}


class DeviceGateway:
    def __init__(self, bus: EventBus, world: World, answer_timeout_s: float):
        self.bus = bus
        self.world = world
        self.answer_timeout_s = answer_timeout_s
        self.sockets: dict[str, WebSocket] = {}
        self._pending: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        # Tests and unattended demos can pre-answer: user -> "yes" | "no" | None (never answers).
        self.autopilot: dict[str, str | None] = {}
        self.today: Callable[[], date] = date.today  # the runtime points this at its clock

    async def _send(self, user: str, msg: dict[str, Any]) -> None:
        self.bus.publish("device.out", msg, source="devices", user=user, task_id=msg.get("task_id"))
        ws = self.sockets.get(user)
        if ws is not None:
            try:
                await ws.send_json(msg)
            except RuntimeError:
                self.sockets.pop(user, None)

    async def haptic(self, user: str, pattern: str) -> None:
        assert pattern in HAPTICS, pattern
        await self._send(user, {"type": "haptic", "pattern": pattern})

    async def say(self, user: str, text: str) -> None:
        await self._send(user, {"type": "speak", "text": text, "expect": "none"})

    async def ask(self, user: str, task_id: str, text: str, channel: Channel) -> dict[str, Any]:
        """Ask a yes/no question on one channel. Returns {"value": yes|no|none, "via": ...}."""
        key = (user, task_id)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[key] = fut

        if channel == Channel.CALL:
            self.bus.publish("call.user", {"text": text}, source="devices", user=user, task_id=task_id)
        else:
            await self.haptic(user, "decision")
        if channel != Channel.TAP:
            await self._send(user, {"type": "speak", "text": text, "expect": "yes_no",
                                    "timeout_s": self.answer_timeout_s, "task_id": task_id,
                                    "channel": channel.name.lower()})
        else:
            await self._send(user, {"type": "approve", "text": text, "task_id": task_id,
                                    "timeout_s": self.answer_timeout_s})

        if user in self.autopilot:
            answer = self.autopilot[user]
            if answer is not None:
                asyncio.get_running_loop().call_later(0.05, self._resolve, user, task_id, answer, "autopilot")
        try:
            return await asyncio.wait_for(fut, timeout=self.answer_timeout_s)
        except asyncio.TimeoutError:
            return {"value": "none", "via": "timeout"}
        finally:
            self._pending.pop(key, None)

    def _resolve(self, user: str, task_id: str, value: str, via: str) -> None:
        fut = self._pending.get((user, task_id))
        if fut and not fut.done():
            fut.set_result({"value": value, "via": via})

    def handle(self, user: str, msg: dict[str, Any]) -> None:
        """A message from a phone (or the virtual device)."""
        kind = msg.get("type")
        if kind == "answer":
            self._resolve(user, msg["task_id"], msg.get("value", "none"), msg.get("via", "unknown"))
            self.bus.publish("user.answer", msg, source="device", user=user, task_id=msg["task_id"])
        elif kind == "wake":
            self.bus.publish("user.wake", {"transcript": msg.get("transcript", "")}, source="device", user=user)
        elif kind == "context":
            profile = self.world.profiles.get(user)
            if profile:
                profile.context.update({k: v for k, v in msg.items() if k != "type"})
            self.bus.publish("ctx.update", msg, source="device", user=user)
        elif kind == "calendar":
            events = parse_events(msg.get("events", []))
            self.world.calendar[user] = events
            flights = sync_flights(self.world, user, events, self.today())
            self.bus.publish("calendar.synced", {"events": len(events), "flights": [f.flight_no for f in flights]},
                             source="device", user=user)

    def pending_for(self, user: str) -> list[str]:
        return [task for (u, task) in self._pending if u == user]
