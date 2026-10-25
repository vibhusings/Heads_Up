"""Record real backend responses as frontend mock fixtures.

Run from backend/:  .venv/bin/python ../frontend/scripts/capture_fixtures.py
The mock API layer replays these, so mock mode never invents shapes.
"""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from headsup.server import app, settings

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src" / "mocks" / "captured.json"
DEMOS = json.loads((HERE.parent / "src" / "mocks" / "demoSignals.json").read_text())


def capture(client: TestClient, name: str, demo: dict) -> dict:
    out = {"reset": client.post("/api/demo/reset", json={"scenario_id": demo["scenario_id"]}).json()}
    client.post("/api/signals", json=demo["signal"]).raise_for_status()
    out["injected"] = client.get("/api/session").json()
    plan = out["injected"]["plans"][0]
    revised = client.patch(
        f"/api/plans/{plan['id']}",
        json={"expected_version": plan["version"], "message_text": f"[edited] {plan['actions'][0]['message_text']}"},
    )
    out["revised"] = revised.json()
    stale = client.post(f"/api/plans/{plan['id']}/approve", json={"expected_version": plan["version"]})
    out["stale_approve"] = {"status": stale.status_code, "body": stale.json()}
    out["approved"] = client.post(
        f"/api/plans/{plan['id']}/approve", json={"expected_version": out["revised"]["version"]}
    ).json()
    out["duplicate_approve"] = client.post(
        f"/api/plans/{plan['id']}/approve", json={"expected_version": out["revised"]["version"]}
    ).json()
    out["after_approve"] = client.get("/api/session").json()
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings.db_path = str(Path(tmp) / "capture.db")
        settings.offline = True
        settings.calendar_mode = "mock"
        with TestClient(app) as client:
            data = {"health": client.get("/api/health").json()}
            for name, demo in DEMOS.items():
                data[name] = capture(client, name, demo)
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
