import asyncio
import time

import pytest

from headsup.config import Settings
from headsup.runtime import Runtime


@pytest.fixture
async def rt():
    s = Settings(offline=True, db_path=":memory:", answer_timeout_s=0.5, supervisor_tick_s=0.1, flight_poll_s=0.1, timekeeper_tick_s=0.1)
    runtime = Runtime(s)
    runtime.devices.autopilot["vibhu"] = "yes"
    runtime.world.profiles["vibhu"].context["airpods_in"] = True
    await runtime.start()
    yield runtime
    await runtime.stop()


async def wait_for(rt: Runtime, kind: str, timeout: float = 8.0, **match) -> dict:
    """Wait until an event of this kind (with matching data fields) is in the log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in rt.log.recent(1000):
            if e["kind"] == kind and all(e["data"].get(k) == v for k, v in match.items()):
                return e
        await asyncio.sleep(0.05)
    kinds = [e["kind"] for e in rt.log.recent(60)]
    raise AssertionError(f"no {kind} {match or ''} within {timeout}s; recent: {kinds}")


def kinds(rt: Runtime) -> list[str]:
    return [e["kind"] for e in rt.log.recent(1000)]
