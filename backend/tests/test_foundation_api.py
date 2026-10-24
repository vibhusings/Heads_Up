from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from headsup.db import FoundationRepository
from headsup.models import Event, Signal
from headsup.server import app, settings


def signal(signal_id: str = "signal_1") -> Signal:
    return Signal(
        id=signal_id,
        kind="location_eta",
        source="test",
        source_mode="mock",
        observed_at=datetime(2026, 10, 25, 1, 40, tzinfo=timezone.utc),
        route_duration_seconds=3600,
    )


def test_contracts_reject_naive_timestamps():
    with pytest.raises(ValidationError):
        Event(
            id="event_1",
            provider="fixture",
            title="Dinner",
            start_at=datetime(2026, 10, 24, 19),
            end_at=datetime(2026, 10, 24, 20),
            timezone="America/Los_Angeles",
        )


def test_repository_reset_and_restart_persistence(tmp_path):
    path = tmp_path / "state.db"
    first_db = sqlite3.connect(path)
    first = FoundationRepository(first_db)
    assert first.session().events[0].id == "event_dinner_001"
    assert first.ingest_signal(signal()).duplicate is False
    first_db.close()

    second_db = sqlite3.connect(path)
    second = FoundationRepository(second_db)
    assert [item.id for item in second.session().signals] == ["signal_1"]
    assert second.ingest_signal(signal()).duplicate is True
    reset = second.reset("foundation")
    assert reset.signals == []
    assert reset.events[0].provider == "fixture"
    second_db.close()


def test_foundation_api_health_session_reset_and_idempotent_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["providers"]["calendar"] == {"mode": "mock", "status": "ok"}

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["events"][0]["id"] == "event_dinner_001"

        payload = signal().model_dump(mode="json")
        first = client.post("/api/signals", json=payload)
        second = client.post("/api/signals", json=payload)
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is True
        assert first.json()["impact_ids"] == ["impact_signal_1"]
        assert first.json()["plan_ids"] == ["plan_signal_1"]

        reset = client.post("/api/demo/reset", json={"scenario_id": "foundation"})
        assert reset.status_code == 200
        assert reset.json()["signals"] == []
        assert client.post("/api/demo/reset", json={"scenario_id": "missing"}).status_code == 404


def test_complete_level_a_flow_persists_and_does_not_duplicate_execution(tmp_path, monkeypatch):
    path = tmp_path / "level-a.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    payload = signal("gate").model_dump(mode="json")

    with TestClient(app) as client:
        assert client.post("/api/demo/reset", json={"scenario_id": "foundation"}).status_code == 200
        created = client.post("/api/signals", json=payload).json()
        assert created["impact_ids"] == ["impact_gate"]
        assert created["plan_ids"] == ["plan_gate"]

        plan = client.get("/api/plans/plan_gate").json()
        original_text = plan["actions"][0]["message_text"]
        revised = client.patch(
            "/api/plans/plan_gate",
            json={"expected_version": 1, "message_text": original_text + " See you then!"},
        )
        assert revised.status_code == 200
        assert revised.json()["version"] == 2
        assert client.post(
            "/api/plans/plan_gate/approve", json={"expected_version": 1}
        ).status_code == 409

        approved = client.post("/api/plans/plan_gate/approve", json={"expected_version": 2})
        assert approved.status_code == 200
        assert approved.json()["plan"]["status"] == "completed"
        assert approved.json()["results"][0]["status"] == "simulated"
        assert approved.json()["duplicate"] is False

        duplicate = client.post("/api/plans/plan_gate/approve", json={"expected_version": 2})
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert len(client.get("/api/actions").json()) == 1

    with TestClient(app) as client:
        session = client.get("/api/session").json()
        assert session["plans"][0]["status"] == "completed"
        assert len(session["results"]) == 1
