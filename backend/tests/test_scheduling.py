import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from headsup.db import FoundationRepository
from headsup.models import IncomingMessage
from headsup.scheduling import ScheduleParseError, build_plan, parse_request
from headsup.server import app, settings


@pytest.fixture
def repository(tmp_path):
    db = sqlite3.connect(tmp_path / "scheduling.db")
    repo = FoundationRepository(db)
    yield repo
    db.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "messaging_mode", "mock")
    with TestClient(app) as value:
        yield value


def message(text: str = "Dinner today at 4:00 PM for 60 minutes?") -> IncomingMessage:
    return IncomingMessage(id="incoming_1", chat_id="chat_sam", contact_id="contact_sam",
                           sender_name="Sam", text=text,
                           received_at=datetime(2026, 10, 24, 15, tzinfo=timezone.utc))


def test_explicit_request_conflicts_and_creates_dependency_plan(repository):
    repository.reset("combined")
    session = repository.session()
    sam = next(item for item in session.contacts if item.id == "contact_sam")
    request = parse_request(message(), sam, "America/New_York")
    impact, plan = build_plan(request, message(), sam, session.events,
                              now=datetime(2026, 10, 24, 15, tzinfo=timezone.utc))
    create, reply = plan.actions
    assert "Project meeting" in impact.reason
    assert create.type == "create_event" and create.tentative
    assert create.new_start_at == datetime(2026, 10, 24, 21, tzinfo=timezone.utc)
    assert reply.depends_on_action_ids == [create.id]
    assert "5:00 PM" in reply.message_text
    assert [item["start_at"] for item in plan.evidence_snapshot["candidate_times"]][:2] == [
        "2026-10-24T21:00:00+00:00", "2026-10-24T23:00:00+00:00",
    ]


def test_ambiguous_request_is_refused(repository):
    repository.reset("combined")
    sam = next(item for item in repository.session().contacts if item.id == "contact_sam")
    with pytest.raises(ScheduleParseError, match="explicit time"):
        parse_request(message("Want to get dinner sometime?"), sam, "America/New_York")


def test_bare_conversational_six_means_evening(repository):
    repository.reset("combined")
    sam = next(item for item in repository.session().contacts if item.id == "contact_sam")
    request = parse_request(message("Can we meet tomorrow at 6?"), sam, "America/New_York")
    assert request.requested_start_at.hour == 22  # 6 PM New York in UTC on this fixture date


def test_poll_api_deduplicates_and_executes_calendar_before_reply(client):
    client.post("/api/demo/reset", json={"scenario_id": "combined"})
    payload = {"messages": [{"id": "incoming_1", "contact_id": "contact_sam",
                              "chat_id": "simulated:contact_sam", "senderName": "Sam",
                              "text": "Dinner today at 4:00 PM for 60 minutes?",
                              "timestamp": "2026-10-24T15:00:00Z"}]}
    assert len(client.post("/api/messaging/poll", json=payload).json()) == 1
    assert client.post("/api/messaging/poll", json=payload).json() == []
    session = client.get("/api/session").json()
    plan = next(item for item in session["plans"] if item["id"] == "plan_incoming_1")
    outcome = client.post(f"/api/plans/{plan['id']}/approve", json={"expected_version": 1}).json()
    assert [item["provider"] for item in outcome["results"]] == ["simulated_calendar", "simulated_outbox"]
    after = client.get("/api/session").json()
    assert any(item["title"] == "Tentative: Dinner with Sam" and item["tentative"] for item in after["events"])
    duplicate = client.post(f"/api/plans/{plan['id']}/approve", json={"expected_version": 1}).json()
    assert duplicate["duplicate"] is True
    assert len(client.get("/api/actions").json()) == 2
