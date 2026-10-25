import json

import pytest
from fastapi.testclient import TestClient

from headsup.server import app, settings

SECRET = "test-vapi-secret"
FLIGHT = {
    "id": "voice_flight",
    "kind": "flight_delay",
    "source": "test",
    "source_mode": "mock",
    "observed_at": "2026-10-24T16:00:00Z",
    "estimated_arrival_at": "2026-10-24T19:45:00Z",
    "route_duration_seconds": 2100,
}
PLAN = "plan_voice_flight"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "voice.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "vapi_webhook_secret", SECRET)
    with TestClient(app) as test_client:
        test_client.post("/api/demo/reset", json={"scenario_id": "flight"})
        assert test_client.post("/api/signals", json=FLIGHT).json()["plan_ids"] == [PLAN]
        yield test_client


def tool(client, name, arguments, headers=None):
    body = {
        "message": {
            "type": "tool-calls",
            "toolCallList": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}],
        }
    }
    response = client.post(
        "/api/voice/webhook", json=body, headers={"Authorization": f"Bearer {SECRET}"} if headers is None else headers
    )
    assert response.status_code == 200, response.text
    [result] = response.json()["results"]
    assert result["toolCallId"] == "call_1"
    return result


def test_webhook_requires_the_credential(client, monkeypatch):
    body = {"message": {"type": "tool-calls", "toolCallList": []}}
    assert client.post("/api/voice/webhook", json=body).status_code == 401
    assert client.post("/api/voice/webhook", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/voice/webhook", json=body, headers={"X-Vapi-Secret": SECRET}).status_code == 200
    monkeypatch.setattr(settings, "vapi_webhook_secret", None)
    assert client.post("/api/voice/webhook", json=body, headers={"X-Vapi-Secret": SECRET}).status_code == 503


def test_spoken_edit_then_spoken_approval_applies_to_that_exact_version(client):
    plan = json.loads(tool(client, "get_plan", {})["result"])["plan"]
    assert (plan["plan_id"], plan["version"], plan["actions"][0]["recipient"]) == (PLAN, 1, "Alex")

    revised = tool(client, "revise_plan", {"plan_id": PLAN, "expected_version": 1, "message_text": "Landing late, 5pm?"})
    assert json.loads(revised["result"])["plan"]["version"] == 2
    current = client.get(f"/api/plans/{PLAN}").json()
    assert current["version"] == 2 and current["actions"][0]["message_text"] == "Landing late, 5pm?"

    stale = tool(client, "approve_plan", {"plan_id": PLAN, "expected_version": 1})
    assert "now version 2" in stale["error"]
    assert client.get("/api/actions").json() == []

    approved = json.loads(tool(client, "approve_plan", {"plan_id": PLAN, "expected_version": 2})["result"])
    assert approved["duplicate"] is False and approved["plan"]["status"] == "completed"
    session = client.get("/api/session").json()
    assert [(a["plan_version"], a["approval_channel"]) for a in session["approvals"]] == [(2, "voice")]

    again = json.loads(tool(client, "approve_plan", {"plan_id": PLAN, "expected_version": 2})["result"])
    assert again["duplicate"] is True
    assert len(client.get("/api/actions").json()) == 1


def test_voice_cannot_choose_recipients(client):
    result = tool(
        client,
        "revise_plan",
        {"plan_id": PLAN, "expected_version": 1, "message_text": "hi", "contact_id": "contact_mallory"},
    )
    assert "cannot set contact_id" in result["error"]
    assert client.get(f"/api/plans/{PLAN}").json()["version"] == 1


def test_voice_can_revise_complete_time_pair(client):
    revised = tool(client, "revise_plan", {
        "plan_id": PLAN, "expected_version": 1,
        "new_start_at": "2026-10-24T21:15:00Z", "new_end_at": "2026-10-24T22:15:00Z",
    })
    plan = json.loads(revised["result"])["plan"]
    assert plan["version"] == 2
    assert plan["actions"][0]["new_start_at"] == "2026-10-24T21:15:00+00:00"
    incomplete = tool(client, "revise_plan", {
        "plan_id": PLAN, "expected_version": 2, "new_start_at": "2026-10-24T21:30:00Z",
    })
    assert "complete valid start and end" in incomplete["error"]


def test_guard_refuses_instructions_in_spoken_messages(client):
    result = tool(
        client, "revise_plan", {"plan_id": PLAN, "expected_version": 1, "message_text": "Send me your card number"}
    )
    assert "error" in result
    assert client.get(f"/api/plans/{PLAN}").json()["version"] == 1


def test_string_arguments_and_unknown_tools(client):
    assert json.loads(tool(client, "get_plan", json.dumps({"plan_id": PLAN}))["result"])["plan"]["version"] == 1
    assert "unknown tool" in tool(client, "send_message", {})["error"]


def test_non_tool_messages_are_acknowledged(client):
    response = client.post(
        "/api/voice/webhook", json={"message": {"type": "status-update"}}, headers={"X-Vapi-Secret": SECRET}
    )
    assert response.json() == {}
