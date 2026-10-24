"""Run the deterministic Level A API flow against a running backend."""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx


SIGNALS = {
    "foundation": {
        "id": "demo_location",
        "kind": "location_eta",
        "source": "demo",
        "source_mode": "mock",
        "observed_at": "2026-10-25T01:40:00Z",
        "route_duration_seconds": 3600,
    },
    "flight": {
        "id": "demo_flight",
        "kind": "flight_delay",
        "source": "demo",
        "source_mode": "mock",
        "observed_at": "2026-10-24T16:00:00Z",
        "estimated_arrival_at": "2026-10-24T19:45:00Z",
        "route_duration_seconds": 2100,
    },
}


def run_demo(client: Any, scenario: str) -> dict[str, Any]:
    reset = client.post("/api/demo/reset", json={"scenario_id": scenario})
    reset.raise_for_status()
    signal = client.post("/api/signals", json=SIGNALS[scenario])
    signal.raise_for_status()
    signal_data = signal.json()
    if not signal_data["plan_ids"]:
        raise RuntimeError("scenario did not produce a plan")

    plan_id = signal_data["plan_ids"][0]
    plan = client.get(f"/api/plans/{plan_id}")
    plan.raise_for_status()
    plan_data = plan.json()
    message = plan_data["actions"][0]["message_text"] + " Thanks for understanding."
    revised = client.patch(
        f"/api/plans/{plan_id}",
        json={"expected_version": plan_data["version"], "message_text": message},
    )
    revised.raise_for_status()
    revised_data = revised.json()
    approved = client.post(
        f"/api/plans/{plan_id}/approve",
        json={"expected_version": revised_data["version"], "user_id": "demo_user", "channel": "script"},
    )
    approved.raise_for_status()
    outcome = approved.json()
    actions = client.get("/api/actions")
    actions.raise_for_status()
    if outcome["plan"]["status"] != "completed" or len(actions.json()) != 1:
        raise RuntimeError("scenario did not complete exactly one action")
    return {
        "scenario": scenario,
        "impact_id": signal_data["impact_ids"][0],
        "plan_id": plan_id,
        "plan_version": revised_data["version"],
        "status": outcome["plan"]["status"],
        "action": actions.json()[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a HeadsUp Level A demo scenario")
    parser.add_argument("--scenario", choices=sorted(SIGNALS), default="foundation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        result = run_demo(client, args.scenario)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
