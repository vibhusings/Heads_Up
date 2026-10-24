"""Hold the line for me: menu, hold, a person picks up, the user nods to join."""

import base64

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for
from headsup import server
from headsup.holdline import choose_heuristic, classify_heuristic, parse_menu

MENU = ("For new reservations, press 1. For changes to an existing trip, press 2. "
        "For baggage, press 3. To speak with an agent, press 0.")


@pytest.fixture(autouse=True)
def fast_business(rt):
    rt.holdline.segment_s = 0.1
    rt.holdline.hold_segments = 3


def say(rt, text):
    rt.devices.handle("vibhu", {"type": "wake", "transcript": text})


def test_line_classification():
    seen: set[str] = set()
    assert classify_heuristic(MENU, seen) == "menu"
    assert classify_heuristic("Please hold. Your call is important to us.", seen) == "hold"
    assert classify_heuristic("Thanks for holding, this is Maria with United. How can I help you today?", seen) == "human"
    assert classify_heuristic("This is a recorded message.", seen) == "other"
    assert classify_heuristic("Music.", {"music."}) == "hold"  # repeated lines are recordings


def test_menu_choice_matches_the_goal():
    options = parse_menu(MENU)
    assert ("2", "changes to an existing trip") in options and ("0", "speak with an agent") in options
    assert choose_heuristic(options, "get me rebooked").digit == "2"
    assert choose_heuristic(options, "ask about a lost bag").digit == "3"
    assert choose_heuristic([("1", "billing")], "get me rebooked").digit == "0"


async def test_holds_then_connects_on_a_nod(rt):
    say(rt, "call United and get me rebooked")
    await wait_for(rt, "hold.connected", business="United Airlines")
    task = next(t for t in rt.tasks.values() if t.kind == "hold_line")
    log = rt.holdline.log[task.id]
    assert ("pressed", "2") in log
    assert any(kind == "said" and "automated assistant" in text for kind, text in log)  # it never pretends to be you
    assert log[-1] == ("bridged", "+15550000001")
    await wait_for(rt, "task.done", outcome="completed")
    # Two taps when the agent picks up, then the whisper.
    asks = [e["data"] for e in rt.log.recent(1000) if e["kind"] == "device.out" and e["data"].get("expect") == "yes_no"]
    assert asks and "agent is on the line" in asks[-1]["text"]


async def test_looping_menu_is_frozen_and_asks_for_an_operator(rt):
    rt.set_chaos("ivr_loop", True)
    say(rt, "call United and get me rebooked")
    await wait_for(rt, "supervisor.alert", problem="loop")
    await wait_for(rt, "doctor.replanned", strategy="operator")
    await wait_for(rt, "hold.connected")
    task = next(t for t in rt.tasks.values() if t.kind == "hold_line")
    assert ("pressed", "0") in rt.holdline.log[task.id]


async def test_endless_hold_asks_the_user_and_hangs_up_on_no(rt):
    rt.set_chaos("hold_forever", True)
    rt.devices.autopilot["vibhu"] = "no"
    rt.planner.hold_line("vibhu", "United Airlines", "+15550000101", "get me rebooked")
    task = next(t for t in rt.tasks.values() if t.kind == "hold_line")
    task.context["hold_limit_s"] = 0.5
    await wait_for(rt, "task.done", outcome="declined by user")
    asks = [e["data"]["text"] for e in rt.log.recent(1000) if e["kind"] == "device.out" and e["data"].get("expect") == "yes_no"]
    assert any("Keep waiting?" in a for a in asks)
    assert rt.holdline.log[task.id][-1] == ("hung_up", "")


async def test_no_answer_when_agent_picks_up_apologizes_and_hangs_up(rt):
    rt.devices.autopilot["vibhu"] = None
    say(rt, "call United and get me rebooked")
    await wait_for(rt, "task.stopped", timeout=10)
    task = next(t for t in rt.tasks.values() if t.kind == "hold_line")
    said = [text for kind, text in rt.holdline.log[task.id] if kind == "said"]
    assert said[-1].startswith("Sorry, they can't join right now")


async def test_unknown_business(rt):
    say(rt, "call Acme Bank and close my account")
    await wait_for(rt, "device.out", text="I don't have a number for Acme Bank.")


class LineTranscriber:
    async def start(self, on_line):
        self.on_line = on_line

    async def feed(self, mulaw):
        await self.on_line(mulaw.decode())

    async def close(self):
        pass


def test_twilio_hold_stream_feeds_the_task(monkeypatch):
    monkeypatch.setattr(server.settings, "db_path", ":memory:")
    monkeypatch.setattr(server.settings, "offline", True)
    with TestClient(server.app) as c:
        rt = c.app.state.rt
        rt.telephony.transcriber = LineTranscriber
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "start": {"streamSid": "MZ2", "customParameters": {
                "mode": "hold", "task_id": "t123", "owner": "vibhu", "other": "United Airlines"}}})
            ws.send_json({"event": "media", "media": {"track": "inbound",
                          "payload": base64.b64encode(b"Please hold.").decode()}})
            ws.send_json({"event": "stop"})
        kinds = [(e["kind"], e["task_id"], e["data"]) for e in rt.log.recent(200) if e["kind"].startswith("hold.")]
        assert ("hold.heard", "t123", {"text": "Please hold.", "business": "United Airlines"}) in kinds
        assert ("hold.stream", "t123", {"up": False}) in kinds
        assert not any(e["kind"] == "call.ended" for e in rt.log.recent(200))
