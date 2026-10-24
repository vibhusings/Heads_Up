"""Leave-now pulses and the end-of-day receipt."""

import asyncio

from conftest import wait_for
from headsup.world import to_min


async def test_leave_now_pulses_with_commitments(rt):
    rt.world.commitments["vibhu"].append("bring the board game")
    rt.clock.set(to_min("18:19"), speed=60)  # dinner 7pm, 35 min away, 5 min buffer: leave at 6:20
    e = await wait_for(rt, "timekeeper.leave_now", timeout=5)
    assert e["data"]["leave_at"] == "6:20pm"
    out = [x["data"] for x in rt.log.recent(500) if x["kind"] == "device.out" and x["user"] == "vibhu"]
    assert {"type": "haptic", "pattern": "leave_now"} in out
    assert any("board game" in (d.get("text") or "") for d in out)


async def test_leave_now_follows_a_moved_plan(rt):
    rt.flight_delays["UA1234"] = 90
    await wait_for(rt, "task.done", outcome="completed")  # dinner moves to 8pm
    rt.clock.set(to_min("19:19"), speed=60)  # old leave time long gone; new one is 7:20
    e = await wait_for(rt, "timekeeper.leave_now", timeout=5)
    assert e["data"]["start"] == "8:00pm"


async def test_receipt_counts_the_day(rt):
    rt.set_chaos("restaurant_no_answer", True)
    rt.flight_delays["UA1234"] = 90
    await wait_for(rt, "task.done", outcome="completed")
    spoken = await rt.timekeeper.send_receipt("vibhu")
    facts = (await wait_for(rt, "timekeeper.receipt"))["data"]
    assert len(facts["handled"]) == 1 and facts["fixed_by_itself"] == 1  # the replan; the retry doesn't count
    assert "fixed them myself" in spoken and "cents" in spoken


async def test_receipt_by_voice_and_at_end_of_day(rt):
    rt.devices.handle("vibhu", {"type": "wake", "transcript": "what did you do today?"})
    await wait_for(rt, "timekeeper.receipt")
    rt.clock.set(to_min("21:29"), speed=120)
    for _ in range(100):
        receipts = [e for e in rt.log.recent(500) if e["kind"] == "timekeeper.receipt"]
        if len(receipts) >= 3:
            break
        await asyncio.sleep(0.05)
    assert [e["user"] for e in receipts] == ["vibhu", "vibhu", "sam"]  # by voice, then both at 9:30pm
