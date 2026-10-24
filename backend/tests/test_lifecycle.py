import sqlite3
from datetime import datetime, timedelta, timezone

from headsup.config import Settings
from headsup.db import FoundationRepository
from headsup.executor import Executor
from headsup.lifecycle import LifecycleService
from headsup.messaging import SimulatedMessaging, UnavailableMessaging, outbox
from headsup.models import Action, Approval, Plan
from headsup.providers.calendar import MockCalendarProvider
from headsup.runtime import Runtime


def plan_with(action: Action, *, plan_id: str = "plan", status: str = "proposed") -> Plan:
    now = datetime.now(timezone.utc)
    return Plan(
        id=plan_id,
        version=1,
        impact_id="impact",
        status=status,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        actions=[action],
    )


def approved(repository: FoundationRepository, plan: Plan) -> None:
    """Store a plan as if it had been approved before the backend stopped."""
    repository.put_plan(plan)
    repository.put("approvals", Approval(id=f"approval_{plan.id}_v{plan.version}", plan_id=plan.id,
                                         plan_version=plan.version, user_id="user",
                                         approved_at=datetime.now(timezone.utc), approval_channel="button"))


def executor_for(repository: FoundationRepository, messaging=None) -> Executor:
    return Executor(repository, messaging or SimulatedMessaging(repository), lambda: MockCalendarProvider([]))


async def test_executor_skips_dependencies_and_keeps_independent_actions(tmp_path):
    db = sqlite3.connect(tmp_path / "lifecycle.db")
    repository = FoundationRepository(db)
    now = datetime.now(timezone.utc)
    plan = Plan(
        id="dependencies",
        version=1,
        impact_id="impact",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        actions=[
            Action(id="calendar", type="send_message", message_text="missing recipient"),
            Action(
                id="dependent",
                type="send_message",
                contact_id="contact_sam",
                message_text="The event moved",
                depends_on_action_ids=["calendar"],
            ),
            Action(id="independent", type="send_message", contact_id="contact_sam", message_text="Update"),
        ],
    )
    repository.put_plan(plan)

    queued = LifecycleService(repository).approve("dependencies", 1, "user", "button")
    assert queued.plan.status == "approved" and queued.results == []

    executor = executor_for(repository)
    plan = await executor.run_plan("dependencies")
    results = repository.results_for_actions([action.id for action in plan.actions])

    assert plan.status == "partially_failed"
    assert [result.status for result in results] == ["failed", "skipped", "simulated"]
    assert results[0].error_code == "INVALID_RECIPIENT"
    assert results[1].error_code == "DEPENDENCY_FAILED"
    duplicate = LifecycleService(repository).approve("dependencies", 1, "user", "button")
    assert duplicate.duplicate is True
    await executor.run_plan("dependencies")
    assert len(repository.session().results) == 3
    db.close()


async def test_restart_resumes_approved_pending_action(tmp_path):
    db = sqlite3.connect(tmp_path / "pending.db")
    repository = FoundationRepository(db)
    approved(repository, plan_with(
        Action(id="pending", type="send_message", contact_id="contact_sam", message_text="Hello"),
        status="approved",
    ))

    assert await executor_for(repository).resume() == ["plan"]

    assert repository.get("plans", "plan").status == "completed"
    assert repository.get("action_results", "pending").status == "simulated"
    assert len(repository.session().results) == 1
    db.close()


async def test_restart_marks_interrupted_write_unknown_without_retry(tmp_path):
    db = sqlite3.connect(tmp_path / "interrupted.db")
    repository = FoundationRepository(db)
    approved(repository, plan_with(
        Action(id="interrupted", type="send_message", contact_id="contact_sam", message_text="Hello",
               status="executing"),
        status="executing",
    ))

    await executor_for(repository).resume()

    result = repository.get("action_results", "interrupted")
    assert repository.get("plans", "plan").status == "failed"
    assert (result.status, result.error_code) == ("unknown", "OUTCOME_UNKNOWN")
    assert outbox(repository) == []
    db.close()


async def test_unavailable_live_messaging_never_falls_back_to_mock(tmp_path):
    db = sqlite3.connect(tmp_path / "live.db")
    repository = FoundationRepository(db)
    contact = repository.get("contacts", "contact_sam")
    repository.put("contacts", contact.model_copy(update={"beeper_chat_id": "chat_sam"}))
    repository.put_plan(
        plan_with(Action(id="live", type="send_message", contact_id="contact_sam", message_text="Hello"))
    )

    LifecycleService(repository).approve("plan", 1, "user", "button")
    plan = await executor_for(repository, UnavailableMessaging()).run_plan("plan")

    result = repository.get("action_results", "live")
    assert plan.status == "failed"
    assert (result.provider_mode, result.error_code) == ("live", "AUTH_REQUIRED")
    assert outbox(repository) == []
    db.close()


async def test_simulated_outbox_uses_action_key_as_idempotency_key(tmp_path):
    db = sqlite3.connect(tmp_path / "outbox.db")
    repository = FoundationRepository(db)
    provider = SimulatedMessaging(repository)
    first = await provider.send_message("key", "contact_sam", "chat", "hello")
    second = await provider.send_message("key", "contact_sam", "chat", "changed")
    assert first == second
    assert [entry.message_text for entry in outbox(repository)] == ["hello"]
    db.close()


async def test_runtime_start_resumes_pending_execution(tmp_path):
    path = tmp_path / "runtime.db"
    db = sqlite3.connect(path)
    repository = FoundationRepository(db)
    approved(repository, plan_with(
        Action(id="startup", type="send_message", contact_id="contact_sam", message_text="Hello"),
        status="approved",
    ))
    db.close()
    runtime = Runtime(
        Settings(
            offline=True,
            db_path=str(path),
            messaging_mode="mock",
            answer_timeout_s=0.1,
            supervisor_tick_s=0.1,
            flight_poll_s=0.1,
            timekeeper_tick_s=0.1,
        )
    )

    await runtime.start()
    try:
        assert runtime.foundation.get("plans", "plan").status == "completed"
        assert runtime.foundation.get("action_results", "startup").status == "simulated"
    finally:
        await runtime.stop()
