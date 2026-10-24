"""Person D, task 5: Beeper messaging against a fake Beeper Desktop API. Nothing real is sent."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from headsup.db import FoundationRepository
from headsup.executor import Executor, action_key
from headsup.lifecycle import LifecycleService
from headsup.messaging import (
    BeeperMessaging, RecipientError, SimulatedMessaging, apply_chat_mappings, map_contact_chat, outbox,
)
from headsup.models import Action, ActionResult, Plan
from headsup.providers.calendar import MockCalendarProvider
from headsup.server import app, settings

SAM_CHAT = "!sam:beeper.local"
GROUP_CHAT = "!group:beeper.local"


class FakeBeeper:
    """Just enough of the Beeper Desktop API. `send` decides what a send does."""

    def __init__(self, send=None):
        self.sent: list[tuple[str, str]] = []
        self.history: dict[str, list[dict]] = {}
        self.send = send or (lambda chat_id: httpx.Response(200, json={"chatID": chat_id, "pendingMessageID": "m1"}))
        self.chats = {SAM_CHAT: {"id": SAM_CHAT, "type": "single", "title": "Sam", "network": "WhatsApp"},
                      GROUP_CHAT: {"id": GROUP_CHAT, "type": "group", "title": "Friends", "network": "WhatsApp"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        parts = request.url.path.split("/")  # ['', 'v1', 'chats', chatID, 'messages']
        chat_id = parts[3] if len(parts) > 3 else None
        if request.method == "POST" and parts[-1] == "messages":
            self.sent.append((chat_id, json.loads(request.content)["text"]))
            return self.send(chat_id)
        if request.method == "GET" and parts[-1] == "messages":
            return httpx.Response(200, json={"items": self.history.get(chat_id, []), "hasMore": False})
        if request.method == "GET" and len(parts) == 4:
            chat = self.chats.get(chat_id)
            return httpx.Response(200, json=chat) if chat else httpx.Response(404, json={})
        return httpx.Response(404)


@pytest.fixture
def repo(tmp_path):
    db = sqlite3.connect(tmp_path / "beeper.db")
    repository = FoundationRepository(db)
    yield repository
    db.close()


def beeper(repo, fake: FakeBeeper) -> BeeperMessaging:
    return BeeperMessaging(repo, "http://beeper.test", "token", transport=httpx.MockTransport(fake))


async def approve_and_run(repo, provider) -> tuple[Plan, list]:
    now = datetime.now(timezone.utc)
    repo.put_plan(Plan(id="plan", version=1, impact_id="impact", created_at=now,
                       expires_at=now + timedelta(minutes=5),
                       actions=[Action(id="msg", type="send_message", contact_id="contact_sam",
                                       event_id="event_dinner_001", message_text="Running 40 min late")]))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    plan = await Executor(repo, provider, lambda: MockCalendarProvider([])).run_plan("plan")
    return plan, repo.session().results


async def map_sam(repo, fake=None) -> None:
    provider = beeper(repo, fake or FakeBeeper())
    await map_contact_chat(repo, provider, "contact_sam", SAM_CHAT)
    await provider.close()


async def test_approved_message_reaches_the_mapped_chat_once(repo):
    fake = FakeBeeper()
    await map_sam(repo, fake)
    plan, [result] = await approve_and_run(repo, beeper(repo, fake))
    assert fake.sent == [(SAM_CHAT, "Running 40 min late")]
    assert (plan.status, result.status, result.provider, result.provider_mode) == (
        "completed", "succeeded", "beeper", "live")
    assert result.provider_reference == f"{SAM_CHAT}:m1"
    [entry] = outbox(repo)
    assert (entry.provider_mode, entry.chat_id) == ("live", SAM_CHAT)


async def test_unmapped_contact_never_reaches_beeper(repo):
    fake = FakeBeeper()
    plan, [result] = await approve_and_run(repo, beeper(repo, fake))
    assert (result.status, result.error_code) == ("failed", "INVALID_RECIPIENT")
    assert fake.sent == []


@pytest.mark.parametrize("response, status, code", [
    (httpx.Response(401), "failed", "AUTH_REQUIRED"),
    (httpx.Response(404), "failed", "INVALID_RECIPIENT"),
    (httpx.Response(429), "failed", "RATE_LIMITED"),
    (httpx.Response(500), "unknown", "OUTCOME_UNKNOWN"),
])
async def test_provider_errors_are_reported_truthfully(repo, response, status, code):
    await map_sam(repo)
    plan, [result] = await approve_and_run(repo, beeper(repo, FakeBeeper(send=lambda chat_id: response)))
    assert (plan.status, result.status, result.error_code) == ("failed", status, code)
    assert (len(outbox(repo)) == 1) == (status == "unknown")  # a definite failure leaves no outbox entry


async def test_beeper_not_running_is_failed_not_sent(repo):
    await map_sam(repo)

    def refuse(chat_id):
        raise httpx.ConnectError("connection refused")

    plan, [result] = await approve_and_run(repo, beeper(repo, FakeBeeper(send=refuse)))
    assert (result.status, result.error_code) == ("failed", "PROVIDER_UNAVAILABLE")
    assert outbox(repo) == []


async def test_send_timeout_is_unknown_and_not_retried(repo):
    await map_sam(repo)

    def hang(chat_id):
        raise httpx.ReadTimeout("no answer")

    fake = FakeBeeper(send=hang)
    provider = beeper(repo, fake)
    plan, [result] = await approve_and_run(repo, provider)
    assert (result.status, result.error_code) == ("unknown", "OUTCOME_UNKNOWN")
    await Executor(repo, provider, lambda: MockCalendarProvider([])).run_plan("plan")
    assert len(fake.sent) == 1


async def crash_mid_send(repo, fake: FakeBeeper, handed_over: bool) -> ActionResult:
    """Claim the action, optionally hand it to Beeper, then 'crash' and resume."""
    await map_sam(repo, fake)
    now = datetime.now(timezone.utc)
    repo.put_plan(Plan(id="plan", version=1, impact_id="impact", created_at=now,
                       expires_at=now + timedelta(minutes=5),
                       actions=[Action(id="msg", type="send_message", contact_id="contact_sam",
                                       message_text="Running late")]))
    LifecycleService(repo).approve("plan", 1, "user", "button")
    provider = beeper(repo, fake)
    executor = Executor(repo, provider, lambda: MockCalendarProvider([]))
    plan = repo.get("plans", "plan")
    key = action_key(plan, plan.actions[0])
    executor._claim(plan, plan.actions[0], key, "beeper")
    if handed_over:
        await provider.send_message(key, "contact_sam", SAM_CHAT, "Running late")
    await executor.resume()
    return repo.session().results[0]


async def test_crash_after_send_is_confirmed_from_chat_history(repo):
    fake = FakeBeeper()
    fake.history[SAM_CHAT] = [{"id": "m9", "isSender": True, "text": "Running late",
                               "timestamp": datetime.now(timezone.utc).isoformat()}]
    result = await crash_mid_send(repo, fake, handed_over=True)
    assert (result.status, result.provider_reference) == ("succeeded", f"{SAM_CHAT}:m9")
    assert len(fake.sent) == 1


async def test_crash_after_send_without_history_is_unknown(repo):
    fake = FakeBeeper()
    result = await crash_mid_send(repo, fake, handed_over=True)
    assert (result.status, result.error_code) == ("unknown", "OUTCOME_UNKNOWN")
    assert len(fake.sent) == 1


async def test_crash_before_handing_over_is_failed_and_never_sent(repo):
    fake = FakeBeeper()
    result = await crash_mid_send(repo, fake, handed_over=False)
    assert (result.status, result.error_code) == ("failed", "PROVIDER_UNAVAILABLE")
    assert fake.sent == []


async def test_mapping_is_verified_and_survives_a_demo_reset(repo):
    await map_sam(repo)
    assert repo.get("contacts", "contact_sam").beeper_chat_id == SAM_CHAT
    repo.reset("foundation")
    apply_chat_mappings(repo)
    assert repo.get("contacts", "contact_sam").beeper_chat_id == SAM_CHAT


@pytest.mark.parametrize("chat_id, provider_kind, code", [
    ("!missing:beeper.local", "beeper", "INVALID_RECIPIENT"),
    (GROUP_CHAT, "beeper", "INVALID_RECIPIENT"),
    (SAM_CHAT, "mock", "AUTH_REQUIRED"),
])
async def test_bad_mappings_are_refused(repo, chat_id, provider_kind, code):
    provider = beeper(repo, FakeBeeper()) if provider_kind == "beeper" else SimulatedMessaging(repo)
    with pytest.raises(RecipientError) as exc:
        await map_contact_chat(repo, provider, "contact_sam", chat_id)
    assert exc.value.code == code
    assert repo.get("contacts", "contact_sam").beeper_chat_id is None


async def test_one_chat_cannot_belong_to_two_contacts(repo):
    await map_sam(repo)
    kim = repo.get("contacts", "contact_sam").model_copy(update={"id": "contact_kim", "beeper_chat_id": None})
    repo.put("contacts", kim)
    with pytest.raises(RecipientError, match="already mapped"):
        await map_contact_chat(repo, beeper(repo, FakeBeeper()), "contact_kim", SAM_CHAT)


def test_api_maps_contact_and_live_health(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "messaging_mode", "live")
    monkeypatch.setattr(settings, "beeper_token", "token")
    with TestClient(app) as client:
        rt = app.state.rt
        rt.messaging.http._transport = httpx.MockTransport(FakeBeeper())
        health = client.get("/api/health").json()
        assert health["providers"]["messaging"] == {"mode": "live", "status": "configured"}

        mapped = client.put("/api/contacts/contact_sam/beeper-chat", json={"chat_id": SAM_CHAT})
        assert mapped.status_code == 200 and mapped.json()["beeper_chat_id"] == SAM_CHAT
        group = client.put("/api/contacts/contact_sam/beeper-chat", json={"chat_id": GROUP_CHAT})
        assert group.status_code == 409
        assert client.put("/api/contacts/nobody/beeper-chat", json={"chat_id": SAM_CHAT}).status_code == 404

        cleared = client.delete("/api/contacts/contact_sam/beeper-chat")
        assert cleared.json()["beeper_chat_id"] is None
