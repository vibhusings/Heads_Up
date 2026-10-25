"""Versioned proposals: revise, reject, and approve an exact version.

Approval only queues the plan; ``executor.Executor`` is the one place that acts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import FoundationRepository
from .executor import approval_id
from .models import Action, Approval, ExecutionOutcome, Plan, PlanPatch


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
            request_id = plan.evidence_snapshot.get("schedule_request_id")
            if request_id and (schedule := self.repository.get("schedule_requests", request_id)) is not None:
                self.repository.put("schedule_requests", schedule.model_copy(update={"status": "rejected"}))
            return rejected

    def approve(
        self, plan_id: str, expected_version: int, user_id: str, approval_channel: str
    ) -> ExecutionOutcome:
        """Record the approval and queue execution in one transaction. Nothing runs here:
        the Executor picks up approved plans, including after a restart."""
        with self.repository.transaction():
            plan = self.get_plan(plan_id)
            self._require_version(plan, expected_version)
            if self.repository.get("approvals", approval_id(plan)) is not None:
                previous = self.repository.results_for_actions([action.id for action in plan.actions])
                return ExecutionOutcome(plan=plan, results=previous, duplicate=True)
            if plan.status != "proposed":
                raise LifecycleError("INVALID_STATE", "only a proposed plan can be approved")
            if plan.expires_at <= datetime.now(timezone.utc):
                expired = plan.model_copy(update={"status": "expired"})
                self.repository.put_plan(expired)
                raise LifecycleError("STALE_PLAN", "plan has expired")

            approval = Approval(
                id=approval_id(plan),
                plan_id=plan.id,
                plan_version=plan.version,
                user_id=user_id,
                approved_at=datetime.now(timezone.utc),
                approval_channel=approval_channel,
            )
            self.repository.put("approvals", approval)
            queued = plan.model_copy(update={"status": "approved"})
            self.repository.put_plan(queued)
            request_id = plan.evidence_snapshot.get("schedule_request_id")
            if request_id and (schedule := self.repository.get("schedule_requests", request_id)) is not None:
                self.repository.put("schedule_requests", schedule.model_copy(update={"status": "approved"}))
            return ExecutionOutcome(plan=queued, results=[], duplicate=False)

    @staticmethod
    def _require_version(plan: Plan, expected_version: int) -> None:
        if plan.version != expected_version:
            raise LifecycleError("STALE_PLAN", "plan version changed; refresh before continuing")
