"""Versioned proposal approval and idempotent simulated execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import FoundationRepository
from .models import Action, ActionResult, Approval, ExecutionOutcome, Plan, PlanPatch


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LifecycleService:
    def __init__(self, repository: FoundationRepository):
        self.repository = repository

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
        with self.repository.transaction():
            plan = self.get_plan(plan_id)
            self._require_version(plan, expected_version)
            previous = self.repository.results_for_actions([action.id for action in plan.actions])
            if plan.status in {"completed", "partially_failed", "failed"} and len(previous) == len(plan.actions):
                return ExecutionOutcome(plan=plan, results=previous, duplicate=True)
            if plan.status != "proposed":
                raise LifecycleError("INVALID_STATE", "only a proposed plan can be approved")
            if plan.expires_at <= datetime.now(timezone.utc):
                expired = plan.model_copy(update={"status": "expired"})
                self.repository.put_plan(expired)
                raise LifecycleError("STALE_PLAN", "plan has expired")

            approval = Approval(
                id=f"approval_{plan.id}_v{plan.version}",
                plan_id=plan.id,
                plan_version=plan.version,
                user_id=user_id,
                approved_at=datetime.now(timezone.utc),
                approval_channel=approval_channel,
            )
            self.repository.put("approvals", approval)
            results: list[ActionResult] = []
            completed_actions: list[Action] = []
            by_action: dict[str, ActionResult] = {}
            for action in plan.actions:
                blocked = any(
                    dependency not in by_action
                    or by_action[dependency].status in {"failed", "unknown", "skipped"}
                    for dependency in action.depends_on_action_ids
                )
                if blocked:
                    result = ActionResult(
                        action_id=action.id,
                        provider="executor",
                        provider_mode="mock",
                        status="skipped",
                        error_code="DEPENDENCY_FAILED",
                        completed_at=datetime.now(timezone.utc),
                    )
                    completed_actions.append(action.model_copy(update={"status": "skipped"}))
                elif action.type == "send_message" and (not action.contact_id or not action.message_text):
                    result = ActionResult(
                        action_id=action.id,
                        provider="simulated_outbox",
                        provider_mode="mock",
                        status="failed",
                        error_code="INVALID_RECIPIENT",
                        completed_at=datetime.now(timezone.utc),
                    )
                    completed_actions.append(action.model_copy(update={"status": "failed"}))
                else:
                    provider = "simulated_outbox" if action.type == "send_message" else "simulated_calendar"
                    result = ActionResult(
                        action_id=action.id,
                        provider=provider,
                        provider_mode="mock",
                        status="simulated",
                        provider_reference=f"outbox:{action.id}",
                        completed_at=datetime.now(timezone.utc),
                    )
                    completed_actions.append(action.model_copy(update={"status": "simulated"}))
                self.repository.put("action_results", result)
                results.append(result)
                by_action[action.id] = result
            successful = sum(result.status in {"succeeded", "simulated"} for result in results)
            failed = len(results) - successful
            status = "completed" if not failed else ("partially_failed" if successful else "failed")
            completed = plan.model_copy(
                update={"status": status, "actions": completed_actions}
            )
            self.repository.put_plan(completed)
            return ExecutionOutcome(plan=completed, results=results, duplicate=False)

    @staticmethod
    def _require_version(plan: Plan, expected_version: int) -> None:
        if plan.version != expected_version:
            raise LifecycleError("STALE_PLAN", "plan version changed; refresh before continuing")
