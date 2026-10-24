import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from headsup.config import Settings
from headsup.db import FoundationRepository
from headsup.lifecycle import LifecycleService
from headsup.models import Action, Plan
from headsup.providers.messaging import MockMessagingProvider, UnavailableMessagingProvider
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


def test_executor_skips_dependencies_and_keeps_independent_actions(tmp_path):
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

    outcome = LifecycleService(repository).approve("dependencies", 1, "user", "button")

    assert outcome.plan.status == "partially_failed"
    assert [result.status for result in outcome.results] == ["failed", "skipped", "simulated"]
    assert outcome.results[1].error_code == "DEPENDENCY_FAILED"
    duplicate = LifecycleService(repository).approve("dependencies", 1, "user", "button")
    assert duplicate.duplicate is True
    assert len(repository.session().results) == 3
    db.close()


def test_restart_resumes_approved_pending_action(tmp_path):
    db = sqlite3.connect(tmp_path / "pending.db")
    repository = FoundationRepository(db)
    repository.put_plan(
        plan_with(
            Action(id="pending", type="send_message", contact_id="contact_sam", message_text="Hello"),
            status="approved",
        )
    )

    outcomes = LifecycleService(repository, MockMessagingProvider()).resume_pending()

    assert outcomes[0].plan.status == "completed"
    assert outcomes[0].results[0].status == "simulated"
    assert len(repository.session().results) == 1
    db.close()


def test_restart_marks_interrupted_write_unknown_without_retry(tmp_path):
    db = sqlite3.connect(tmp_path / "interrupted.db")
    repository = FoundationRepository(db)
    provider = MockMessagingProvider()
    repository.put_plan(
        plan_with(
            Action(
                id="interrupted",
                type="send_message",
                contact_id="contact_sam",
                message_text="Hello",
                status="executing",
            ),
            status="executing",
        )
    )

    outcomes = LifecycleService(repository, provider).resume_pending()

    assert outcomes[0].plan.status == "failed"
    assert outcomes[0].results[0].status == "unknown"
    assert outcomes[0].results[0].error_code == "OUTCOME_UNKNOWN"
    assert provider.sent == {}
    db.close()


def test_unavailable_live_messaging_never_falls_back_to_mock(tmp_path):
    db = sqlite3.connect(tmp_path / "live.db")
    repository = FoundationRepository(db)
    contact = repository.get("contacts", "contact_sam")
    repository.put("contacts", contact.model_copy(update={"beeper_chat_id": "chat_sam"}))
    repository.put_plan(
        plan_with(Action(id="live", type="send_message", contact_id="contact_sam", message_text="Hello"))
    )

    outcome = LifecycleService(repository, UnavailableMessagingProvider()).approve("plan", 1, "user", "button")

    assert outcome.plan.status == "failed"
    assert outcome.results[0].provider_mode == "live"
    assert outcome.results[0].error_code == "AUTH_REQUIRED"
    db.close()


def test_mock_provider_uses_action_id_as_idempotency_key():
    provider = MockMessagingProvider()
    first = provider.send("chat", "hello", "action")
    second = provider.send("chat", "changed", "action")
    assert first == second
    assert len(provider.sent) == 1


@pytest.mark.asyncio
async def test_runtime_start_resumes_pending_execution(tmp_path):
    path = tmp_path / "runtime.db"
    db = sqlite3.connect(path)
    repository = FoundationRepository(db)
    repository.put_plan(
        plan_with(
            Action(id="startup", type="send_message", contact_id="contact_sam", message_text="Hello"),
            status="approved",
        )
    )
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
