"""Person D: approval, execution and messaging reliability (master plan section 14)."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from headsup.db import FoundationRepository
from headsup.executor import Executor, action_key
from headsup.lifecycle import LifecycleError, LifecycleService
from headsup.messaging import SendResult, SimulatedMessaging, UnavailableMessaging, outbox
from headsup.models import Action, Plan, PlanPatch, Signal
from headsup.providers.calendar import MockCalendarProvider
from headsup.server import app, settings


def make_plan(*actions: Action, plan_id: str = "plan") -> Plan:
    now = datetime.now(timezone.utc)
    return Plan(id=plan_id, version=1, impact_id="impact", created_at=now,
                expires_at=now + timedelta(minutes=5), actions=list(actions))


def message(action_id: str = "msg", contact_id: str | None = "contact_sam", **extra) -> Action:
    return Action(id=action_id, type="send_message", contact_id=contact_id,
                  event_id=extra.pop("event_id", "event_dinner_001"), message_text="Running late", **extra)


@pytest.fixture
def repo(tmp_path):
    db = sqlite3.connect(tmp_path / "executor.db")
    yield FoundationRepository(db)
    db.close()


def executor_for(repo, messaging=None, calendar=None, **kw) -> Executor:
    calendar = calendar or MockCalendarProvider(repo.session().events)
    return Executor(repo, messaging or SimulatedMessaging(repo), lambda: calendar, **kw)


class RecordingMessaging(SimulatedMessaging):
    """Counts provider calls; optionally hangs to simulate a send that times out."""

    def __init__(self, repo, hang: bool = False):
        super().__init__(repo)
        self.calls = 0
        self.hang = hang

    async def send_message(self, action_key, contact_id, chat_id, text):
        self.calls += 1
        if self.hang:
            await asyncio.sleep(10)
        return await super().send_message(action_key, contact_id, chat_id, text)


class LiveRecording(RecordingMessaging):
    name = "beeper"
    mode = "live"

    async def reconcile(self, action_key):
        return None  # a live provider with no history lookup cannot confirm delivery


async def test_approval_queues_and_executor_writes_one_labeled_outbox_entry(repo):
    repo.put_plan(make_plan(message()))
    queued = LifecycleService(repo).approve("plan", 1, "user", "button")
    assert queued.plan.status == "approved"
    assert outbox(repo) == []  # approval alone never acts

    plan = await executor_for(repo).run_plan("plan")
    assert plan.status == "completed"
    assert plan.actions[0].status == "simulated"
    [entry] = outbox(repo)
    assert entry.provider_mode == "mock"
    assert entry.chat_id == "simulated:contact_sam"
    assert entry.action_key == "plan:v1:msg"


async def test_duplicate_approvals_produce_one_outcome(repo):
    repo.put_plan(make_plan(message()))
    lifecycle, executor = LifecycleService(repo), executor_for(repo)
    first = lifecycle.approve("plan", 1, "user", "button")
    second = lifecycle.approve("plan", 1, "user", "voice")
    assert (first.duplicate, second.duplicate) == (False, True)
    await asyncio.gather(executor.run_plan("plan"), executor.run_plan("plan"))
    assert len(outbox(repo)) == 1
    assert len(repo.session().approvals) == 1


async def test_edit_after_approval_needs_new_approval(repo):
    repo.put_plan(make_plan(message("msg_v1")))
    lifecycle, executor = LifecycleService(repo), executor_for(repo)
    lifecycle.approve("plan", 1, "user", "button")
    revised = lifecycle.revise("plan", PlanPatch(expected_version=1, message_text="Edited"))
    assert (revised.version, revised.status) == (2, "proposed")

    assert (await executor.run_plan("plan")).status == "proposed"  # v1 approval does not cover v2
    assert outbox(repo) == []
    with pytest.raises(LifecycleError) as stale:
        lifecycle.approve("plan", 1, "user", "button")
    assert stale.value.code == "STALE_PLAN"

    lifecycle.approve("plan", 2, "user", "button")
    await executor.run_plan("plan")
    assert [entry.message_text for entry in outbox(repo)] == ["Edited"]


async def test_executed_plan_cannot_be_revised(repo):
    repo.put_plan(make_plan(message()))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    await executor_for(repo).run_plan("plan")
    with pytest.raises(LifecycleError) as exc:
        LifecycleService(repo).revise("plan", PlanPatch(expected_version=1, message_text="late edit"))
    assert exc.value.code == "INVALID_STATE"


@pytest.mark.parametrize("contact_id", ["contact_unknown", None, "contact_kim"])
async def test_missing_or_unrelated_contact_blocks_sending(repo, contact_id):
    kim = repo.session().contacts[0].model_copy(update={"id": "contact_kim", "display_name": "Kim"})
    repo.put("contacts", kim)  # in the directory, but not invited to the dinner
    repo.put_plan(make_plan(message(contact_id=contact_id)))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = await executor_for(repo).run_plan("plan")
    [result] = repo.session().results
    assert (plan.status, result.status, result.error_code) == ("failed", "failed", "INVALID_RECIPIENT")
    assert outbox(repo) == []


async def test_live_mode_needs_a_verified_chat(repo):
    repo.put_plan(make_plan(message()))  # fixture contact has no beeper_chat_id
    LifecycleService(repo).approve("plan", 1, "user", "button")
    live = LiveRecording(repo)
    await executor_for(repo, messaging=live).run_plan("plan")
    assert repo.session().results[0].error_code == "INVALID_RECIPIENT"
    assert live.calls == 0


async def test_disconnected_live_provider_shows_failed_never_sent(repo):
    contact = repo.session().contacts[0].model_copy(update={"beeper_chat_id": "!chat:beeper"})
    repo.put("contacts", contact)
    repo.put_plan(make_plan(message()))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = await executor_for(repo, messaging=UnavailableMessaging()).run_plan("plan")
    [result] = repo.session().results
    assert (plan.status, result.status, result.provider_mode) == ("failed", "failed", "live")
    assert result.error_code == "PROVIDER_UNAVAILABLE"


async def test_send_timeout_is_unknown_and_never_retried(repo):
    repo.put_plan(make_plan(message()))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    hanging = RecordingMessaging(repo, hang=True)
    plan = await executor_for(repo, messaging=hanging, timeout_s=0.05).run_plan("plan")
    [result] = repo.session().results
    assert (plan.status, result.status, result.error_code) == ("failed", "unknown", "OUTCOME_UNKNOWN")
    await executor_for(repo, messaging=hanging).run_plan("plan")
    assert hanging.calls == 1


async def test_restart_resumes_approved_work(tmp_path):
    path = tmp_path / "restart.db"
    first = FoundationRepository(sqlite3.connect(path))
    first.put_plan(make_plan(message()))
    LifecycleService(first).approve("plan", 1, "user", "button")
    first.db.close()  # backend dies after approval, before execution

    second = FoundationRepository(sqlite3.connect(path))
    assert await executor_for(second).resume() == ["plan"]
    assert second.get("plans", "plan").status == "completed"
    assert len(outbox(second)) == 1
    second.db.close()


async def test_crash_mid_send_reconciles_or_marks_unknown(tmp_path):
    path = tmp_path / "crash.db"
    repo = FoundationRepository(sqlite3.connect(path))
    plan = make_plan(message())
    repo.put_plan(plan)
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = repo.get("plans", "plan")
    executor = executor_for(repo)
    executor._claim(plan, plan.actions[0], action_key(plan, plan.actions[0]), "simulated_outbox")
    repo.put_plan(plan.model_copy(update={"status": "executing"}))  # ...and the process died here

    # Simulated outbox never got the message: the attempt is unknown, not resent.
    recording = RecordingMessaging(repo)
    await executor_for(repo, messaging=recording).run_plan("plan")
    assert repo.session().results[0].status == "unknown"
    assert recording.calls == 0
    repo.db.close()


async def test_crash_after_simulated_send_is_reconciled_from_outbox(repo):
    repo.put_plan(make_plan(message()))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = repo.get("plans", "plan")
    key = action_key(plan, plan.actions[0])
    executor = executor_for(repo)
    executor._claim(plan, plan.actions[0], key, "simulated_outbox")
    await SimulatedMessaging(repo).send_message(key, "contact_sam", "simulated:contact_sam", "Running late")

    await executor.run_plan("plan")
    [result] = repo.session().results
    assert result.status == "simulated"
    assert len(outbox(repo)) == 1


async def test_calendar_failure_skips_dependent_message(repo):
    event = repo.session().events[0].model_copy(update={"organizer_self": False})
    repo.put("app_events", event)
    start = event.start_at + timedelta(hours=1)
    move = Action(id="move", type="update_event", event_id=event.id, new_start_at=start,
                  new_end_at=start + (event.end_at - event.start_at))
    moved_msg = message("moved_msg", depends_on_action_ids=["move"])
    repo.put_plan(make_plan(move, moved_msg))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = await executor_for(repo, calendar=MockCalendarProvider([event])).run_plan("plan")
    results = repo.results_for_actions(["move", "moved_msg"])
    assert [r.status for r in results] == ["failed", "skipped"]
    assert results[0].error_code == "PERMISSION_DENIED"
    assert plan.status == "failed"
    assert outbox(repo) == []


async def test_calendar_success_then_message(repo):
    event = repo.session().events[0]
    start = event.start_at + timedelta(hours=1)
    end = start + (event.end_at - event.start_at)
    move = Action(id="move", type="update_event", event_id=event.id, new_start_at=start, new_end_at=end)
    repo.put_plan(make_plan(move, message("moved_msg", depends_on_action_ids=["move"])))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = await executor_for(repo).run_plan("plan")
    assert plan.status == "completed"
    assert [r.status for r in repo.session().results] == ["simulated", "simulated"]
    assert repo.get("app_events", event.id).start_at == start


async def test_stale_event_version_rejects_execution(repo):
    event = repo.session().events[0]
    start = event.start_at + timedelta(hours=1)
    move = Action(id="move", type="update_event", event_id=event.id, new_start_at=start,
                  new_end_at=start + (event.end_at - event.start_at))
    plan = make_plan(move).model_copy(update={"evidence_snapshot": {"event_versions": {event.id: "old"}}})
    repo.put_plan(plan)
    LifecycleService(repo).approve("plan", 1, "user", "button")
    await executor_for(repo).run_plan("plan")
    assert repo.session().results[0].error_code == "STALE_PLAN"
    assert repo.get("app_events", event.id).start_at == event.start_at


def test_api_approve_executes_and_exposes_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "messaging_mode", "mock")
    signal = Signal(id="late", kind="location_eta", source="test", source_mode="mock",
                    observed_at=datetime(2026, 10, 25, 1, 40, tzinfo=timezone.utc), route_duration_seconds=3600)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["providers"]["messaging"] == {"mode": "mock", "status": "ok"}
        client.post("/api/signals", json=signal.model_dump(mode="json"))
        approved = client.post("/api/plans/plan_late/approve", json={"expected_version": 1}).json()
        assert approved["plan"]["status"] == "completed"
        [entry] = client.get("/api/outbox").json()
        assert entry["provider_mode"] == "mock"
        assert entry["contact_id"] == "contact_sam"
        assert client.post("/api/plans/plan_late/approve", json={"expected_version": 1}).json()["duplicate"]
        assert len(client.get("/api/outbox").json()) == 1


def test_api_live_messaging_without_beeper_is_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "live.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "messaging_mode", "live")
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "degraded"
        assert health["providers"]["messaging"]["error_code"] == "PROVIDER_UNAVAILABLE"
