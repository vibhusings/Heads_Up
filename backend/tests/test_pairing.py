from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from headsup.pairing import PairingStore
from headsup.server import app, settings

NOW = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)


def test_minted_token_expires_and_is_replaced():
    store = PairingStore(ttl=timedelta(minutes=10))
    assert store.status(NOW) == "disabled"
    first, expires_at = store.mint(NOW)
    assert expires_at == NOW + timedelta(minutes=10)
    assert store.valid(first, NOW) and store.status(NOW) == "paired"
    assert not store.valid(first, NOW + timedelta(minutes=10))
    second, _ = store.mint(NOW)
    assert store.valid(second, NOW) and not store.valid(first, NOW)
    assert not store.valid(None, NOW) and not store.valid("guess", NOW)


def test_static_token_still_works():
    store = PairingStore(static_token="paired")
    assert store.valid("paired") and store.status() == "configured"


def local_client(tmp_path, monkeypatch, host="127.0.0.1"):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "pairing.db"))
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "calendar_mode", "mock")
    monkeypatch.setattr(settings, "pairing_token", None)
    return TestClient(app, client=(host, 50000))


def test_pairing_is_minted_locally_and_unlocks_location(tmp_path, monkeypatch):
    with local_client(tmp_path, monkeypatch) as client:
        sample = {
            "latitude": 32.88,
            "longitude": -117.23,
            "accuracy_meters": 15,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        assert client.post("/api/location", json=sample).status_code == 401
        pairing = client.post("/api/pairing").json()
        assert client.get("/api/health").json()["providers"]["phone_pairing"]["status"] == "paired"
        accepted = client.post("/api/location", json=sample, headers={"X-Pairing-Token": pairing["token"]})
        assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
        blurry = {**sample, "accuracy_meters": 900, "latitude": 32.9}
        assert client.post("/api/location", json=blurry, headers={"X-Pairing-Token": pairing["token"]}).json()[
            "status"
        ] == "uncertain"


def test_pairing_refuses_tunnelled_and_remote_requests(tmp_path, monkeypatch):
    with local_client(tmp_path, monkeypatch) as client:
        assert client.post("/api/pairing", headers={"cf-connecting-ip": "203.0.113.9"}).status_code == 403
        assert client.post("/api/pairing", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 403
    with local_client(tmp_path, monkeypatch, host="203.0.113.9") as client:
        assert client.post("/api/pairing").status_code == 403
