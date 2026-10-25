"""Runtime: builds every agent and service and holds the shared state.

Also runs the guardian: if the Supervisor itself stops beating, every task
pauses until it is back.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .agents.base import Agent
from .agents.concierge import Concierge
from .agents.context import Extractor, Researcher
from .agents.thinkers import Judge, Planner, Triage
from .agents.timekeeper import Timekeeper
from .agents.watchers import FlightWatcher
from .agents.workers import Liaison, TaskRunner, Verifier
from .brain import Brain
from .chaos import Chaos
from .clock import Clock
from .config import Settings
from .contracts import Task
from .devices import DeviceGateway
from .doctor import Doctor
from .db import FoundationRepository
from .events import EventBus, EventLog
from .holdline import HoldLineRunner, SimBusiness, TwilioHoldLine
from .executor import Executor
from .ledger import Ledger
from .lifecycle import LifecycleService
from .messaging import BeeperMessaging, SimulatedMessaging, UnavailableMessaging, apply_chat_mappings
from .pairing import PairingStore
from .services import SMS, AviationStackSource, SimFlightSource, SimRestaurants
from .supervisor import Supervisor
from .telephony import Telephony
from .wearables import WearableBridge
from .world import World, demo_world
from .providers.calendar import (
    FREEBUSY_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    GoogleCalendarProvider,
    GoogleTokenStore,
    MockCalendarProvider,
    UnavailableCalendarProvider,
)


class Runtime:
    def __init__(self, settings: Settings, world: World | None = None):
        self.settings = settings
        self.log = EventLog(settings.db_path)
        self.foundation = FoundationRepository(self.log.db)
        apply_chat_mappings(self.foundation)
        self.messaging = self._messaging_provider()
        self.lifecycle = LifecycleService(self.foundation)
        self.pairing = PairingStore(settings.pairing_token)
        self.bus = EventBus(self.log)
        self.ledger = Ledger(settings.daily_budget_usd)
        self.brain = Brain(settings, self.ledger, self.bus)
        self.world = world or demo_world()
        self.chaos = Chaos(self.bus)
        self.devices = DeviceGateway(self.bus, self.world, settings.answer_timeout_s)

        # Outside world. Real adapters slot in front of the simulators when keys exist.
        self.flight_delays: dict[str, int] = {}
        # Real flight data, or the two simulated feeds; never a mix. A real feed that fails
        # must not fall back to made-up delays: with no backup, the Doctor marks the data stale.
        if settings.aviationstack_key:
            sources = [AviationStackSource(settings.aviationstack_key, settings.flight_real_poll_s)]
        else:
            sources = [SimFlightSource("primary", self.chaos, self.flight_delays),
                       SimFlightSource("backup", self.chaos, self.flight_delays)]
        self.restaurants = SimRestaurants(self.chaos)
        self.sms = SMS(settings.twilio_sid, settings.twilio_token, settings.twilio_from)
        self.telephony = Telephony(settings, self.bus, self.world)
        self.holdline = (TwilioHoldLine(settings, self.bus) if settings.twilio_ready
                         else SimBusiness(self.bus, self.chaos, segment_s=settings.hold_sim_segment_s))
        self.clock = Clock()
        self.devices.today = self.clock.today
        self.calendar = self._calendar_provider()
        self.executor = Executor(self.foundation, self.messaging, lambda: self.calendar)

        # Shared state.
        self.tasks: dict[str, Task] = {}
        self.runners: dict[str, TaskRunner] = {}
        self.heartbeats: dict[str, float] = {}
        self.locks: dict[str, str] = {}
        self.paused = False

        # Agents and helpers.
        self.flight_watcher = FlightWatcher(self, sources, interval_s=settings.flight_poll_s)
        self.triage = Triage(self)
        self.extractor = Extractor(self)
        self.supervisor = Supervisor(self)
        self.doctor = Doctor(self)
        self.planner = Planner(self)
        self.judge = Judge(self)
        self.researcher = Researcher(self)
        self.liaison = Liaison(self)
        self.verifier = Verifier(self)
        self.concierge = Concierge(self)
        self.timekeeper = Timekeeper(self)
        self.wearables = WearableBridge(self)
        self.agents: dict[str, Agent] = {a.name: a for a in (
            self.flight_watcher, self.triage, self.extractor, self.concierge, self.timekeeper,
            self.supervisor, self.doctor)}
        self._bg: list[asyncio.Task] = []

    def _calendar_provider(self):
        if self.settings.calendar_mode == "mock":
            return MockCalendarProvider(self.foundation.session().events)
        if self.settings.calendar_mode != "live":
            return UnavailableCalendarProvider("CALENDAR_MODE must be mock or live")
        if not self.settings.google_calendar_token_file:
            return UnavailableCalendarProvider()
        token_file = Path(self.settings.google_calendar_token_file).expanduser()
        if not token_file.is_file():
            return UnavailableCalendarProvider("Google Calendar token file does not exist")
        scopes = [WRITE_SCOPE if self.settings.google_calendar_write_enabled else READ_SCOPE]
        if self.settings.google_calendar_freebusy_enabled:
            scopes.append(FREEBUSY_SCOPE)
        return GoogleCalendarProvider(
            self.settings.google_calendar_id,
            GoogleTokenStore(token_file, scopes),
            scopes=scopes,
        )

    def _messaging_provider(self):
        # Live messaging is Beeper Desktop; without a token "live" fails visibly, never falls back.
        if self.settings.messaging_mode == "mock":
            return SimulatedMessaging(self.foundation)
        if self.settings.messaging_mode == "live":
            if not self.settings.beeper_token:
                return UnavailableMessaging("BEEPER_ACCESS_TOKEN is not set")
            return BeeperMessaging(self.foundation, self.settings.beeper_url, self.settings.beeper_token)
        return UnavailableMessaging("MESSAGING_MODE must be mock or live")

    def reset_foundation(self, scenario_id: str):
        state = self.foundation.reset(scenario_id)
        apply_chat_mappings(self.foundation)
        state = self.foundation.session()
        if self.settings.calendar_mode == "mock":
            self.calendar = MockCalendarProvider(state.events)
        return state

    # ---- lifecycle ----

    async def start(self) -> None:
        recovered = await self.executor.resume()  # finish work approved before a restart
        for agent in self.agents.values():
            agent.start()
        self._bg.append(asyncio.create_task(self._guardian()))
        self.bus.publish("system.started", {"offline": self.settings.offline, "agents": list(self.agents),
                                            "budget_usd": self.ledger.daily_budget_usd,
                                            "recovered_executions": len(recovered)}, source="runtime")

    async def stop(self) -> None:
        background = self._bg + self._runner_tasks()
        for t in background:
            t.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        await asyncio.gather(*(agent.stop() for agent in self.agents.values()))
        await self.calendar.close()
        if isinstance(self.messaging, BeeperMessaging):
            await self.messaging.close()

    def _runner_tasks(self) -> list[asyncio.Task]:
        return [t for t in asyncio.all_tasks() if t.get_name().startswith(("runner-", "wearable-"))]

    async def restart_agent(self, name: str) -> None:
        agent = self.agents[name]
        await agent.stop()
        agent.start()
        self.bus.publish("agent.restarted", {"agent": name}, source="runtime")

    async def _guardian(self) -> None:
        while True:
            await asyncio.sleep(1)
            beat = self.heartbeats.get("supervisor", 0)
            down = time.time() - beat > self.settings.heartbeat_timeout_s
            if down != self.paused:
                self.paused = down
                self.bus.publish("system.paused" if down else "system.resumed", {}, source="guardian")

    def set_chaos(self, fault: str, on: bool) -> None:
        self.chaos.set(fault, on)
        if fault == "cost_spike":
            self.ledger.multiplier = 25.0 if on else 1.0

    # ---- tasks ----

    def spawn(self, task: Task) -> TaskRunner:
        runner = (HoldLineRunner if task.kind == "hold_line" else TaskRunner)(self, task)
        self.tasks[task.id] = task
        self.runners[task.id] = runner
        asyncio.create_task(runner.run(), name=f"runner-{task.id}")
        return runner

    def acquire_lock(self, resource: str, task_id: str) -> bool:
        holder = self.locks.setdefault(resource, task_id)
        return holder == task_id

    def release_locks(self, task_id: str) -> None:
        for resource in [r for r, holder in self.locks.items() if holder == task_id]:
            del self.locks[resource]

    # ---- playbook ----

    def playbook_get(self, signature: str) -> str | None:
        row = self.log.db.execute("SELECT repair FROM playbook WHERE signature = ?", (signature,)).fetchone()
        return row[0] if row else None

    def playbook_save(self, signature: str, repair: str) -> None:
        self.log.db.execute(
            "INSERT INTO playbook VALUES (?, ?, 1, ?) ON CONFLICT(signature) DO UPDATE SET "
            "repair = excluded.repair, wins = wins + 1, updated = excluded.updated",
            (signature, repair, time.time()),
        )
        self.log.db.commit()

    def playbook(self) -> list[dict]:
        rows = self.log.db.execute("SELECT signature, repair, wins FROM playbook ORDER BY updated DESC").fetchall()
        return [{"signature": s, "repair": r, "wins": w} for s, r, w in rows]

    # ---- summary ----

    def snapshot(self) -> dict:
        return {
            "offline": self.settings.offline,
            "paused": self.paused,
            "degraded": self.brain.degraded,
            "spend": {"total_usd": round(self.ledger.total_usd, 4), "budget_usd": self.ledger.daily_budget_usd},
            "flight_source": self.flight_watcher.source_name,
            "chaos": self.chaos.flags,
            "tasks": [{"id": t.id, "user": t.user, "kind": t.kind, "state": t.state.value, "goal": t.contract.goal,
                       "reached": t.reached, "milestones": t.contract.milestones, "repairs": t.repairs,
                       "spent_usd": round(self.ledger.by_task[t.id], 4)} for t in self.tasks.values()],
            "playbook": self.playbook(),
            "agents": {name: round(time.time() - ts, 1) for name, ts in self.heartbeats.items()},
        }
