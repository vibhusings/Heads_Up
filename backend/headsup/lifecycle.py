"""Versioned proposal approval and idempotent simulated execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import FoundationRepository
from .models import Action, ActionResult, Approval, ExecutionOutcome, Plan, PlanPatch
from .providers.messaging import MessagingProvider, MockMessagingProvider, SendResult


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LifecycleService:
    def __init__(self, repository: FoundationRepository, messaging: MessagingProvider | None = None):
        self.repository = repository
        self.messaging = messaging or MockMessagingProvider()

    def get_plan(self, plan_id: str) -> Plan:
        plan = self.repository.get("plans", plan_id)
        if plan is None:
            raise LifecycleError("NOT_FOUND", "plan not found")
        return plan

    def revise(self, plan_id: str, patch: PlanPatch) -> Plan:
        with self.repository.transaction():
            plan = self.get_plan(plan_id)
            self._require_version(plan, patch.expected_version)
            if plan.status not in {"proposed", "approved"}:
                raise LifecycleError("INVALID_STATE", "only a proposed or pending approved plan can be revised")
            next_version = plan.version + 1
            id_map = {action.id: f"{action.id.rsplit('_v', 1)[0]}_v{next_version}" for action in plan.actions}
            actions: list[Action] = []
            for old in plan.actions:
                update = {
                    "id": id_map[old.id],
                    "status": "pending",
                    "depends_on_action_ids": [id_map.get(item, item) for item in old.depends_on_action_ids],
                }
                if patch.message_text is not None and old.type == "send_message":
                    update["message_text"] = patch.message_text
                if patch.new_start_at is not None:
                    update["new_start_at"] = patch.new_start_at
                    update["new_end_at"] = patch.new_end_at
                actions.append(old.model_copy(update=update))
            revised = plan.model_copy(
                update={
                    "version": next_version,
                    "status": "proposed",
                    "actions": actions,
                    "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                }
            )
            self.repository.put_plan(revised)
            return revised

    def reject(self, plan_id: str, expected_version: int) -> Plan:
        with self.repository.transaction():
            plan = self.get_plan(plan_id)
            self._require_version(plan, expected_version)
            if plan.status == "rejected":
                return plan
            if plan.status != "proposed":
                raise LifecycleError("INVALID_STATE", "only a proposed plan can be rejected")
            rejected = plan.model_copy(update={"status": "rejected"})
            self.repository.put_plan(rejected)
            return rejected

    def approve(
        self, plan_id: str, expected_version: int, user_id: str, approval_channel: str
    ) -> ExecutionOutcome:
        created_approval = False
        with self.repository.transaction():
            plan = self.get_plan(plan_id)
            self._require_version(plan, expected_version)
            previous = self.repository.results_for_actions([action.id for action in plan.actions])
            if plan.status in {"completed", "partially_failed", "failed"} and len(previous) == len(plan.actions):
                return ExecutionOutcome(plan=plan, results=previous, duplicate=True)
            if plan.status not in {"proposed", "approved", "executing"}:
                raise LifecycleError("INVALID_STATE", "only a proposed plan can be approved")
            if plan.status == "proposed" and plan.expires_at <= datetime.now(timezone.utc):
                expired = plan.model_copy(update={"status": "expired"})
                self.repository.put_plan(expired)
                raise LifecycleError("STALE_PLAN", "plan has expired")
            if plan.status == "proposed":
                approval = Approval(
                    id=f"approval_{plan.id}_v{plan.version}",
                    plan_id=plan.id,
                    plan_version=plan.version,
                    user_id=user_id,
                    approved_at=datetime.now(timezone.utc),
                    approval_channel=approval_channel,
                )
                self.repository.put("approvals", approval)
                plan = plan.model_copy(update={"status": "approved"})
                self.repository.put_plan(plan)
                created_approval = True

        outcome = self.execute_pending(plan_id)
        return outcome.model_copy(update={"duplicate": not created_approval})

    def execute_pending(self, plan_id: str) -> ExecutionOutcome:
        plan = self.get_plan(plan_id)
        results = {item.action_id: item for item in self.repository.results_for_actions(
            [action.id for action in plan.actions]
        )}
        if plan.status in {"completed", "partially_failed", "failed"} and len(results) == len(plan.actions):
            return ExecutionOutcome(plan=plan, results=list(results.values()), duplicate=True)
        if plan.status not in {"approved", "executing"}:
            raise LifecycleError("INVALID_STATE", "plan is not approved for execution")
        if plan.status == "approved":
            plan = plan.model_copy(update={"status": "executing"})
            self.repository.put_plan(plan)

        for action in plan.actions:
            if action.id in results:
                continue
            blocked = any(
                dependency not in results
                or results[dependency].status in {"failed", "unknown", "skipped"}
                for dependency in action.depends_on_action_ids
            )
            if blocked:
                result = self._result(action, "executor", "mock", "skipped", error_code="DEPENDENCY_FAILED")
            elif action.status == "executing":
                result = self._result(action, "executor", "mock", "unknown", error_code="OUTCOME_UNKNOWN")
            elif action.type == "send_message":
                result = self._execute_message(plan, action)
                plan = self.get_plan(plan.id)
            else:
                result = self._result(
                    action,
                    "simulated_calendar",
                    "mock",
                    "simulated",
                    provider_reference=f"calendar:{action.id}",
                )
            plan = self._record_result(plan, action.id, result)
            results[action.id] = result

        ordered = [results[action.id] for action in plan.actions if action.id in results]
        successful = sum(result.status in {"succeeded", "simulated"} for result in ordered)
        failed = len(ordered) - successful
        status = "completed" if not failed else ("partially_failed" if successful else "failed")
        completed = plan.model_copy(update={"status": status})
        self.repository.put_plan(completed)
        return ExecutionOutcome(plan=completed, results=ordered, duplicate=False)

    def resume_pending(self) -> list[ExecutionOutcome]:
        outcomes: list[ExecutionOutcome] = []
        for plan in self.repository.session().plans:
            if plan.status in {"approved", "executing"}:
                outcomes.append(self.execute_pending(plan.id))
        return outcomes

    def _execute_message(self, plan: Plan, action: Action) -> ActionResult:
        contact = self.repository.get("contacts", action.contact_id) if action.contact_id else None
        if contact is None or not action.message_text:
            return self._result(
                action, "messaging", self.messaging.mode, "failed", error_code="INVALID_RECIPIENT"
            )
        chat_id = contact.beeper_chat_id
        if self.messaging.mode == "mock":
            chat_id = chat_id or contact.id
        if not chat_id:
            return self._result(
                action, "messaging", self.messaging.mode, "failed", error_code="INVALID_RECIPIENT"
            )

        executing = action.model_copy(update={"status": "executing"})
        self.repository.put_plan(self._replace_action(plan, executing))
        try:
            provider_result = self.messaging.send(chat_id, action.message_text, action.id)
        except Exception:
            provider_result = SendResult(
                provider="messaging",
                provider_mode=self.messaging.mode,
                status="unknown",
                error_code="OUTCOME_UNKNOWN",
                retry_safe=False,
            )
        return ActionResult(
            action_id=action.id,
            provider=provider_result.provider,
            provider_mode=provider_result.provider_mode,
            status=provider_result.status,
            provider_reference=provider_result.provider_reference,
            error_code=provider_result.error_code,
            completed_at=datetime.now(timezone.utc),
        )

    def _record_result(self, plan: Plan, action_id: str, result: ActionResult) -> Plan:
        action = next(item for item in plan.actions if item.id == action_id)
        updated = action.model_copy(update={"status": result.status})
        plan = self._replace_action(plan, updated)
        with self.repository.transaction():
            self.repository.put("action_results", result)
            self.repository.put_plan(plan)
        return plan

    @staticmethod
    def _replace_action(plan: Plan, replacement: Action) -> Plan:
        return plan.model_copy(
            update={"actions": [replacement if item.id == replacement.id else item for item in plan.actions]}
        )

    @staticmethod
    def _result(
        action: Action,
        provider: str,
        provider_mode: str,
        status: str,
        provider_reference: str | None = None,
        error_code: str | None = None,
    ) -> ActionResult:
        return ActionResult(
            action_id=action.id,
            provider=provider,
            provider_mode=provider_mode,
            status=status,
            provider_reference=provider_reference,
            error_code=error_code,
            completed_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _require_version(plan: Plan, expected_version: int) -> None:
        if plan.version != expected_version:
            raise LifecycleError("STALE_PLAN", "plan version changed; refresh before continuing")
