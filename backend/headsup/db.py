"""SQLite product-state repository and fixture seeding."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import (
    Action,
    ActionResult,
    Approval,
    Contact,
    Event,
    Impact,
    Plan,
    SessionState,
    Signal,
    SignalResult,
)


FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
SESSION_ID = "demo"

MODELS = {
    "contacts": Contact,
    "app_events": Event,
    "signals": Signal,
    "impacts": Impact,
    "plans": Plan,
    "actions": Action,
    "action_results": ActionResult,
    "approvals": Approval,
}
KEYS = {
    "contacts": "id",
    "app_events": "id",
    "signals": "id",
    "impacts": "id",
    "plans": "id",
    "actions": "id",
    "action_results": "action_id",
    "approvals": "id",
}
FIXTURE_KEYS = {**{name: name for name in MODELS}, "app_events": "events", "action_results": "results"}


class FoundationRepository:
    """Owns only HeadsUp application state; provider data is never deleted here."""

    def __init__(self, db: sqlite3.Connection, fixtures: Path = FIXTURES):
        self.db = db
        self.fixtures = fixtures
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self.migrate()
        if not self._session_exists(SESSION_ID):
            self.reset("foundation")

    def migrate(self) -> None:
        with self._lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS app_events (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS impacts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS action_results (action_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS app_events_session ON app_events(session_id);
                CREATE INDEX IF NOT EXISTS signals_session ON signals(session_id);
                CREATE INDEX IF NOT EXISTS plans_session ON plans(session_id);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
                """
            )
            self.db.commit()

    def _session_exists(self, session_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None

    def _fixture(self, scenario_id: str) -> dict[str, Any]:
        path = self.fixtures / f"{scenario_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown scenario: {scenario_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def reset(self, scenario_id: str) -> SessionState:
        """Replace local demo state only. This never invokes or mutates a provider."""
        raw = self._fixture(scenario_id)
        validated: dict[str, list[Any]] = {}
        for table, model in MODELS.items():
            fixture_key = FIXTURE_KEYS[table]
            validated[table] = [model.model_validate(item) for item in raw.get(fixture_key, [])]

        with self.transaction():
            for table in MODELS:
                self.db.execute(f"DELETE FROM {table} WHERE session_id = ?", (SESSION_ID,))
            self.db.execute(
                "INSERT INTO sessions(id, scenario_id) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET scenario_id = excluded.scenario_id, created_at = CURRENT_TIMESTAMP",
                (SESSION_ID, scenario_id),
            )
            for table, values in validated.items():
                key = KEYS[table]
                for value in values:
                    payload = value.model_dump_json()
                    self.db.execute(
                        f"INSERT INTO {table}({key}, session_id, payload) VALUES (?, ?, ?)",
                        (getattr(value, key), SESSION_ID, payload),
                    )
        return self.session()

    def _list(self, table: str) -> list[Any]:
        model = MODELS[table]
        rows = self.db.execute(
            f"SELECT payload FROM {table} WHERE session_id = ? ORDER BY rowid", (SESSION_ID,)
        ).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def session(self) -> SessionState:
        row = self.db.execute("SELECT scenario_id FROM sessions WHERE id = ?", (SESSION_ID,)).fetchone()
        if row is None:
            raise RuntimeError("foundation session is not initialized")
        return SessionState(
            id=SESSION_ID,
            scenario_id=row[0],
            contacts=self._list("contacts"),
            events=self._list("app_events"),
            signals=self._list("signals"),
            impacts=self._list("impacts"),
            plans=self._list("plans"),
            actions=self._list("actions"),
            results=self._list("action_results"),
            approvals=self._list("approvals"),
        )

    def ingest_signal(
        self,
        signal: Signal,
        processor: Callable[[Signal, list[Event], list[Contact]], tuple[list[Impact], list[Plan]]] | None = None,
    ) -> SignalResult:
        """Persist a normalized signal and return any outcomes produced by Person C's engine.

        The current engine integration is intentionally narrow: Person C can persist Impact and
        Plan objects through ``put`` and this endpoint discovers those linked records. Duplicate
        signal IDs are safe and never create another record.
        """
        duplicate = self.db.execute("SELECT 1 FROM signals WHERE id = ?", (signal.id,)).fetchone() is not None
        if not duplicate:
            with self.transaction():
                self.put("signals", signal)
                if processor is not None:
                    impacts, plans = processor(signal, self._list("app_events"), self._list("contacts"))
                    for impact in impacts:
                        self.put("impacts", impact)
                    for plan in plans:
                        self.put_plan(plan)
        impacts = [item for item in self._list("impacts") if signal.id in item.signal_ids]
        impact_ids = {item.id for item in impacts}
        plans = [item for item in self._list("plans") if item.impact_id in impact_ids]
        return SignalResult(
            signal_id=signal.id,
            duplicate=duplicate,
            impact_ids=[item.id for item in impacts],
            plan_ids=[item.id for item in plans],
        )

    def put(self, table: str, value: Any) -> None:
        if table not in MODELS:
            raise KeyError(table)
        parsed = MODELS[table].model_validate(value)
        key = KEYS[table]
        with self.transaction():
            self.db.execute(
                f"INSERT INTO {table}({key}, session_id, payload) VALUES (?, ?, ?) "
                f"ON CONFLICT({key}) DO UPDATE SET payload = excluded.payload",
                (getattr(parsed, key), SESSION_ID, parsed.model_dump_json()),
            )

    def put_plan(self, plan: Plan) -> None:
        with self.transaction():
            self.put("plans", plan)
            for action in plan.actions:
                self.put("actions", action)

    def get(self, table: str, entity_id: str) -> Any | None:
        if table not in MODELS:
            raise KeyError(table)
        key = KEYS[table]
        row = self.db.execute(
            f"SELECT payload FROM {table} WHERE {key} = ? AND session_id = ?", (entity_id, SESSION_ID)
        ).fetchone()
        return MODELS[table].model_validate_json(row[0]) if row else None

    def results_for_actions(self, action_ids: list[str]) -> list[ActionResult]:
        return [result for action_id in action_ids if (result := self.get("action_results", action_id)) is not None]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            outermost = self._transaction_depth == 0
            if outermost:
                self.db.execute("BEGIN")
            self._transaction_depth += 1
            try:
                yield
            except Exception:
                self._transaction_depth -= 1
                if outermost:
                    self.db.rollback()
                raise
            else:
                self._transaction_depth -= 1
                if outermost:
                    self.db.commit()
