"""The single executor: the only layer that writes to the outside world.

Approval queues a plan (status "approved"); the executor carries it out one
action at a time. Before any provider call it saves a unique action key, so a
crash or restart can tell "never started" from "started, outcome unknown". An
action that may have reached the provider is never retried blindly: it is
reconciled against the provider or marked unknown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from .db import SESSION_ID, FoundationRepository
from .messaging import MessagingProvider, RecipientError, SendResult, resolve_recipient
from .models import Action, ActionResult, Plan
from .providers.calendar import CalendarChanges, CalendarProvider

PENDING = {"approved", "executing"}
SUCCESS = {"succeeded", "simulated"}


class ActionError(Exception):
    """The action cannot be attempted; the code says why. No provider was called."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def approval_id(plan: Plan) -> str:
    return f"approval_{plan.id}_v{plan.version}"


def action_key(plan: Plan, action: Action) -> str:
    return f"{plan.id}:v{plan.version}:{action.id}"


class Executor:
    def __init__(
        self,
        repository: FoundationRepository,
        messaging: MessagingProvider,
        calendar: Callable[[], CalendarProvider],
        timeout_s: float = 15.0,
    ):
        self.repository = repository
        self.messaging = messaging
        self.calendar = calendar  # a getter: the runtime swaps the calendar provider on demo reset
        self.timeout_s = timeout_s
        self._lock = asyncio.Lock()  # one executor at a time, whoever asks

    async def resume(self) -> list[str]:
        """Finish every plan that was approved but not completed, e.g. after a restart."""
        pending = [plan.id for plan in self.repository.session().plans if plan.status in PENDING]
        for plan_id in pending:
            await self.run_plan(plan_id)
        return pending

    async def run_plan(self, plan_id: str) -> Plan | None:
        async with self._lock:
            with self.repository.transaction():
                plan = self.repository.get("plans", plan_id)
                if plan is None or plan.status not in PENDING:
                    return plan
                approval = self.repository.get("approvals", approval_id(plan))
                if approval is None or approval.plan_version != plan.version:
                    return plan  # no approval for this exact version: nothing may run
                if plan.status == "approved":
                    plan = plan.model_copy(update={"status": "executing"})
                    self.repository.put_plan(plan)

            results: dict[str, ActionResult] = {}
            for action in plan.actions:
                result = await self._run_action(plan, action, results)
                results[action.id] = result
                plan = self._set_action_status(plan, action.id, result.status)

            ok = sum(result.status in SUCCESS for result in results.values())
            status = "completed" if ok == len(results) else ("partially_failed" if ok else "failed")
            plan = plan.model_copy(update={"status": status})
            self.repository.put_plan(plan)
            return plan

    # ---- one action ----

    async def _run_action(self, plan: Plan, action: Action, done: dict[str, ActionResult]) -> ActionResult:
        existing = self.repository.get("action_results", action.id)
        if existing is not None:
            return existing
        if any(dep not in done or done[dep].status not in SUCCESS for dep in action.depends_on_action_ids):
            return self._record(action, "executor", "skipped", error_code="DEPENDENCY_FAILED")

        key = action_key(plan, action)
        if self._attempt_started(key) or action.status == "executing":
            return await self._recover(action, key)

        try:
            call, provider = self._prepare(plan, action)
        except RecipientError as exc:
            return self._record(action, self.messaging.name, "failed", error_code=exc.code)
        except ActionError as exc:
            return self._record(action, "executor", "failed", error_code=exc.code)

        self._claim(plan, action, key, provider)
        try:
            sent = await asyncio.wait_for(call(key), timeout=self.timeout_s)
        except Exception:  # noqa: BLE001 - timeout or crash mid-call: it may have been delivered
            sent = SendResult(status="unknown", error_code="OUTCOME_UNKNOWN")
        return self._record(action, provider, sent.status, sent.provider_reference, sent.error_code, key)

    def _prepare(self, plan: Plan, action: Action):
        """Validate everything we can before touching a provider; return the call to make."""
        event = self.repository.get("app_events", action.event_id) if action.event_id else None
        if action.type == "send_message":
            if not action.message_text:
                raise ActionError("INVALID_ACTION")
            contacts = self.repository.session().contacts
            chat_id = resolve_recipient(action.contact_id, contacts, event, self.messaging.mode)

            async def send(key: str) -> SendResult:
                return await self.messaging.send_message(key, action.contact_id, chat_id, action.message_text)

            return send, self.messaging.name

        if action.type == "update_event":
            if event is None:
                raise ActionError("STALE_PLAN")
            if action.new_start_at is None or action.new_end_at is None:
                raise ActionError("INVALID_ACTION")
            calendar = self.calendar()
            versions = plan.evidence_snapshot.get("event_versions", {})
            expected = versions.get(event.id, event.provider_version) or ""
            try:
                changes = CalendarChanges(new_start_at=action.new_start_at, new_end_at=action.new_end_at,
                                          timezone=event.timezone)
            except ValueError as exc:
                raise ActionError("INVALID_ACTION") from exc
            provider = "simulated_calendar" if calendar.mode == "mock" else "google_calendar"

            async def update(key: str) -> SendResult:
                result = await calendar.update_event(event.provider_event_id or event.id, expected, changes)
                if result.status == "succeeded" and result.event is not None:
                    self.repository.put("app_events", result.event.model_copy(update={"id": event.id}))
                status = "simulated" if result.status == "succeeded" and calendar.mode == "mock" else result.status
                return SendResult(status=status, provider_reference=result.provider_reference,
                                  error_code=result.error_code, retry_safe=result.retry_safe)

            return update, provider

        raise ActionError("INVALID_ACTION")

    async def _recover(self, action: Action, key: str) -> ActionResult:
        """An earlier run claimed this action and stopped before recording a result."""
        row = self.repository.db.execute(
            "SELECT provider FROM action_attempts WHERE action_key = ?", (key,)
        ).fetchone()
        if row is None:  # marked executing without an attempt row: still never resend
            return self._record(action, "executor", "unknown", error_code="OUTCOME_UNKNOWN")
        provider = row[0]
        found = await self.messaging.reconcile(key) if provider == self.messaging.name else None
        if found is not None:
            return self._record(action, provider, found.status, found.provider_reference, found.error_code, key)
        return self._record(action, provider, "unknown", error_code="OUTCOME_UNKNOWN", key=key)

    # ---- persistence ----

    def _attempt_started(self, key: str) -> bool:
        return self.repository.db.execute(
            "SELECT 1 FROM action_attempts WHERE action_key = ?", (key,)
        ).fetchone() is not None

    def _claim(self, plan: Plan, action: Action, key: str, provider: str) -> None:
        """Saved and committed before the provider is called."""
        with self.repository.transaction():
            self.repository.db.execute(
                "INSERT INTO action_attempts VALUES (?, ?, ?, ?, ?, ?, 'executing', ?, NULL)",
                (key, SESSION_ID, plan.id, plan.version, action.id, provider, _now().isoformat()),
            )
            self.repository.put("actions", action.model_copy(update={"status": "executing"}))

    def _record(
        self,
        action: Action,
        provider: str,
        status: str,
        provider_reference: str | None = None,
        error_code: str | None = None,
        key: str | None = None,
    ) -> ActionResult:
        mode = self.messaging.mode if provider == self.messaging.name else (
            "live" if provider == "google_calendar" else "mock")
        result = ActionResult(action_id=action.id, provider=provider, provider_mode=mode, status=status,
                              provider_reference=provider_reference, error_code=error_code, completed_at=_now())
        with self.repository.transaction():
            self.repository.put("action_results", result)
            if key is not None:
                self.repository.db.execute(
                    "UPDATE action_attempts SET state = ?, finished_at = ? WHERE action_key = ?",
                    (status, result.completed_at.isoformat(), key),
                )
        return result

    def _set_action_status(self, plan: Plan, action_id: str, status: str) -> Plan:
        actions = [item.model_copy(update={"status": status}) if item.id == action_id else item
                   for item in plan.actions]
        plan = plan.model_copy(update={"actions": actions})
        self.repository.put_plan(plan)
        return plan


def _now() -> datetime:
    return datetime.now(timezone.utc)
