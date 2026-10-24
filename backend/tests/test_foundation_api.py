from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from headsup.db import FoundationRepository
from headsup.engine import process
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


def test_flight_fixture_is_ready_for_the_second_demo_scenario(tmp_path):
    db = sqlite3.connect(tmp_path / "flight.db")
    repository = FoundationRepository(db)
    state = repository.reset("flight")
    assert [event.id for event in state.events] == ["event_meeting_001", "event_later_001"]
    flight = Signal(
        id="flight_demo",
        kind="flight_delay",
        source="fixture",
        source_mode="mock",
        observed_at=datetime(2026, 10, 24, 16, tzinfo=timezone.utc),
        estimated_arrival_at=datetime(2026, 10, 24, 19, 45, tzinfo=timezone.utc),
        route_duration_seconds=35 * 60,
    )
    result = repository.ingest_signal(flight, process)
    assert result.impact_ids == ["impact_flight_demo"]
    assert repository.session().impacts[0].late_by_seconds == 50 * 60
    db.close()


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


def test_location_requires_pairing_and_rejects_stale_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "location.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "pairing_token", "paired")
    fresh = datetime.now(timezone.utc)
    payload = {
        "latitude": 38.9,
        "longitude": -77.0,
        "accuracy_meters": 20,
        "captured_at": fresh.isoformat(),
    }

    with TestClient(app) as client:
        assert client.post("/api/location", json=payload).status_code == 401
        accepted = client.post("/api/location", json=payload, headers={"X-Pairing-Token": "paired"})
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"

        stale = payload | {"captured_at": (fresh - timedelta(minutes=3)).isoformat()}
        response = client.post("/api/location", json=stale, headers={"X-Pairing-Token": "paired"})
        assert response.json() == {"status": "stale", "signal": None, "error_code": "STALE_LOCATION"}

        uncertain = payload | {"accuracy_meters": 500, "captured_at": datetime.now(timezone.utc).isoformat()}
        response = client.post("/api/location", json=uncertain, headers={"X-Pairing-Token": "paired"})
        assert response.json()["status"] == "uncertain"


def test_rejection_never_executes_and_frontend_origin_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "reject.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    with TestClient(app) as client:
        created = client.post("/api/signals", json=signal("reject").model_dump(mode="json")).json()
        plan_id = created["plan_ids"][0]
        rejected = client.post(f"/api/plans/{plan_id}/reject", json={"expected_version": 1})
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert client.post(f"/api/plans/{plan_id}/approve", json={"expected_version": 1}).status_code == 409
        assert client.get("/api/actions").json() == []

        preflight = client.options(
            "/api/signals",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
