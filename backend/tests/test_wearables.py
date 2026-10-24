"""Wearable bridge: plans reach the AirPods and watch, decisions come back for the exact version."""

import time

import pytest
from fastapi.testclient import TestClient

from headsup import server
from headsup.providers.messaging import UnavailableMessagingProvider

DINNER_LATE = {"id": "late_1", "kind": "location_eta", "source": "manual", "source_mode": "mock",
               "observed_at": "2026-10-25T01:40:00Z", "route_duration_seconds": 3600}
PLAN = "plan_late_1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.settings, "db_path", ":memory:")
    monkeypatch.setattr(server.settings, "offline", True)
    monkeypatch.setattr(server.settings, "answer_timeout_s", 3.0)
    monkeypatch.setattr(server.settings, "messaging_mode", "mock")
    with TestClient(server.app) as c:
        rt = c.app.state.rt
        rt.world.profiles["vibhu"].context["airpods_in"] = True
        c.post("/api/demo/reset", json={"scenario_id": "foundation"})
        yield c


def rt(c):
    return c.app.state.rt


def events(c, kind=None):
    return [e for e in rt(c).log.recent(2000) if kind is None or e["kind"] == kind]


def wait_until(check, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def asked(c):
    """The device question that is waiting, as the phone would see it."""
    return wait_until(lambda: rt(c).devices.pending_for("vibhu"))[0]


def answer(c, value, via):
    return c.post("/sim/answer", json={"user": "vibhu", "value": value, "via": via})


def plan(c):
    return c.get(f"/api/plans/{PLAN}").json()


def device_out(c):
    return [e["data"] for e in events(c, "device.out")]


def test_new_plan_taps_the_watch_and_whispers_the_proposal(client):
    client.post("/api/signals", json=DINNER_LATE)
    assert asked(client) == f"plan:{PLAN}:v1"
    out = device_out(client)
    assert {"type": "haptic", "pattern": "decision"} in out
    speak = next(d for d in out if d.get("expect") == "yes_no")
    assert speak["task_id"] == f"plan:{PLAN}:v1" and speak["channel"] == "whisper"
    assert speak["text"].startswith("You'll be about 40 minutes late to Dinner with Sam. Send Sam: Hey Sam,")
    assert speak["text"].endswith("Nod to send, or shake to skip.")


def test_nod_approves_that_version_and_confirms_with_a_soft_tap(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    answer(client, "yes", "nod")
    wait_until(lambda: plan(client)["status"] == "completed")
    approvals = rt(client).foundation.session().approvals
    assert [(a.plan_version, a.approval_channel) for a in approvals] == [(1, "nod")]
    wait_until(lambda: {"type": "haptic", "pattern": "handled"} in device_out(client))
    assert any("demo outbox" in (d.get("text") or "") for d in device_out(client))


def test_shake_rejects_and_sends_nothing(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    answer(client, "no", "shake")
    wait_until(lambda: plan(client)["status"] == "rejected")
    assert rt(client).foundation.session().results == []


def test_watch_screen_tap_in_a_meeting(client):
    rt(client).world.profiles["vibhu"].context.update({"in_meeting": True})
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    assert any(d.get("type") == "approve" for d in device_out(client))  # shown on the watch, not spoken
    answer(client, "yes", "double_tap")
    wait_until(lambda: plan(client)["status"] == "completed")
    assert rt(client).foundation.session().approvals[0].approval_channel == "watch"


def test_no_answer_means_no_action(client):
    rt(client).devices.answer_timeout_s = 0.3
    client.post("/api/signals", json=DINNER_LATE)
    wait_until(lambda: events(client, "wearable.no_answer"))
    assert plan(client)["status"] == "proposed"
    assert rt(client).foundation.session().approvals == []


def test_editing_the_plan_replaces_the_question_with_the_new_version(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    client.patch(f"/api/plans/{PLAN}", json={"expected_version": 1, "message_text": "Hey Sam, 40 min late, sorry!"})
    wait_until(lambda: rt(client).devices.pending_for("vibhu") == [f"plan:{PLAN}:v2"])
    answer(client, "yes", "nod")
    wait_until(lambda: plan(client)["status"] == "completed")
    assert rt(client).foundation.session().approvals[0].plan_version == 2


def test_a_late_answer_for_an_old_version_is_refused(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    client.patch(f"/api/plans/{PLAN}", json={"expected_version": 1, "message_text": "Edited"})
    wait_until(lambda: rt(client).devices.pending_for("vibhu") == [f"plan:{PLAN}:v2"])
    bridge = rt(client).wearables
    future = client.portal.call(bridge.decide, PLAN, 1, "yes", "nod")  # an answer to the old question
    assert future is None
    refused = wait_until(lambda: events(client, "wearable.refused"))
    assert refused[0]["data"]["code"] == "STALE_PLAN"
    assert plan(client)["status"] == "proposed" and plan(client)["version"] == 2


def test_button_approval_stops_the_wearable_question(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    client.post(f"/api/plans/{PLAN}/approve", json={"expected_version": 1})
    wait_until(lambda: rt(client).devices.pending_for("vibhu") == [])
    assert answer(client, "yes", "nod").status_code == 409  # nothing is waiting any more


def test_failed_send_gives_the_long_buzz(client, monkeypatch):
    monkeypatch.setattr(rt(client).lifecycle, "messaging", UnavailableMessagingProvider())  # live send fails
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    answer(client, "yes", "nod")
    wait_until(lambda: {"type": "haptic", "pattern": "urgent"} in device_out(client))
    assert any("didn't go through" in (d.get("text") or "") for d in device_out(client))


def test_no_verified_contact_is_spoken_but_never_asked(client):
    repo = rt(client).foundation
    dinner = repo.get("app_events", "event_dinner_001")
    repo.put("app_events", dinner.model_copy(update={"contact_ids": []}))
    client.post("/api/signals", json=DINNER_LATE)
    wait_until(lambda: events(client, "wearable.blocked"))
    assert rt(client).devices.pending_for("vibhu") == []
    assert any("no verified contact" in (d.get("text") or "") for d in device_out(client))


def test_voice_commands_read_and_approve_the_pending_plan(client):
    rt(client).devices.answer_timeout_s = 0.3
    client.post("/api/signals", json=DINNER_LATE)
    wait_until(lambda: events(client, "wearable.no_answer"))
    client.post("/sim/wake", json={"user": "vibhu", "transcript": "what's pending?"})
    wait_until(lambda: len(events(client, "wearable.asked")) == 2)  # read out again
    wait_until(lambda: len(events(client, "wearable.no_answer")) == 2)
    client.post("/sim/wake", json={"user": "vibhu", "transcript": "approve it"})
    wait_until(lambda: plan(client)["status"] == "completed")
    assert rt(client).foundation.session().approvals[0].approval_channel == "voice"


def test_duplicate_signal_does_not_ask_twice(client):
    client.post("/api/signals", json=DINNER_LATE)
    asked(client)
    client.post("/api/signals", json=DINNER_LATE)
    time.sleep(0.3)
    assert len(events(client, "wearable.asked")) == 1
