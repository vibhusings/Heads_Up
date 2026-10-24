"""HTTP and WebSocket surface.

  /ws/device/{user}     the iPhone app (intents out, answers and context in)
  /events               Server-Sent Events feed for the judges' screen
  /state                snapshot of tasks, spend, playbook, agents
  /sim/*                drive the demo: flight delays, calls, virtual-device answers
  /chaos/{fault}        break things on purpose
  /twilio/*             calls placed through Heads Up
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .chaos import FAULTS
from .config import settings
from .runtime import Runtime
from .telephony import CallSession, MediaBridge, owner_leg_twiml, valid_twilio_signature, whisper_twiml
from .world import to_min

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = Runtime(settings)
    app.state.rt = rt
    await rt.start()
    yield
    await rt.stop()


app = FastAPI(title="Heads Up", lifespan=lifespan)


def rt_of(request_or_ws) -> Runtime:
    return request_or_ws.app.state.rt


# ---------- device ----------

@app.websocket("/ws/device/{user}")
async def device(ws: WebSocket, user: str):
    rt = rt_of(ws)
    if user not in rt.world.profiles:
        await ws.close(code=4404)
        return
    await ws.accept()
    rt.devices.sockets[user] = ws
    rt.bus.publish("device.connected", {}, source="devices", user=user)
    try:
        while True:
            rt.devices.handle(user, await ws.receive_json())
    except WebSocketDisconnect:
        pass
    finally:
        if rt.devices.sockets.get(user) is ws:
            del rt.devices.sockets[user]
        rt.bus.publish("device.disconnected", {}, source="devices", user=user)


# ---------- judges' screen ----------

@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/state")
async def state(request: Request):
    return rt_of(request).snapshot()


@app.get("/events")
async def events(request: Request):
    rt = rt_of(request)
    sub = rt.bus.subscribe()

    async def stream():
        try:
            for e in rt.log.recent(100):
                yield f"data: {json.dumps(e, default=str)}\n\n"
            while not await request.is_disconnected():
                try:
                    e = await asyncio.wait_for(sub.get(), timeout=15)
                    yield f"data: {e.to_json()}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            rt.bus.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------- simulation ----------

class FlightDelay(BaseModel):
    flight_no: str = "UA1234"
    minutes: int


class Answer(BaseModel):
    user: str
    task_id: str | None = None  # defaults to whatever the user is being asked
    value: str  # yes | no
    via: str = "virtual_device"


class Context(BaseModel):
    user: str
    airpods_in: bool | None = None
    in_meeting: bool | None = None


class SimCall(BaseModel):
    user: str = "vibhu"
    other: str = "Sam"
    lines: list[dict[str, str]]  # [{"speaker": "vibhu", "text": "..."}]


class Wake(BaseModel):
    user: str = "vibhu"
    transcript: str


@app.post("/sim/flight-delay")
async def sim_flight_delay(body: FlightDelay, request: Request):
    rt_of(request).flight_delays[body.flight_no] = body.minutes
    return {"ok": True}


@app.post("/sim/answer")
async def sim_answer(body: Answer, request: Request):
    rt = rt_of(request)
    task_id = body.task_id or next(iter(rt.devices.pending_for(body.user)), None)
    if task_id is None:
        raise HTTPException(409, "nothing is waiting for an answer")
    rt.devices.handle(body.user, {"type": "answer", "task_id": task_id, "value": body.value, "via": body.via})
    return {"ok": True, "task_id": task_id}


@app.post("/sim/context")
async def sim_context(body: Context, request: Request):
    msg = {"type": "context", **body.model_dump(exclude={"user"}, exclude_none=True)}
    rt_of(request).devices.handle(body.user, msg)
    return {"ok": True}


@app.post("/sim/call")
async def sim_call(body: SimCall, request: Request):
    rt = rt_of(request)
    call = CallSession(rt.bus, body.user, body.other)
    for line in body.lines:
        call.add_line(line["speaker"], line["text"])
        await asyncio.sleep(0.3)
    call.end()
    return {"ok": True}


@app.post("/sim/wake")
async def sim_wake(body: Wake, request: Request):
    rt_of(request).devices.handle(body.user, {"type": "wake", "transcript": body.transcript})
    return {"ok": True}


# ---------- chaos ----------

@app.get("/chaos")
async def chaos_list():
    return FAULTS


@app.post("/chaos/{fault}")
async def chaos_set(fault: str, request: Request, on: bool = True):
    try:
        rt_of(request).set_chaos(fault, on)
    except KeyError:
        raise HTTPException(404, f"unknown fault {fault}")
    return {"fault": fault, "on": on}


class Clock(BaseModel):
    time: str  # "HH:MM"
    speed: float = 1.0  # 60 = one minute per second
    date: str | None = None  # "YYYY-MM-DD"; today when omitted


@app.post("/sim/clock")
async def sim_clock(body: Clock, request: Request):
    rt = rt_of(request)
    rt.clock.set(to_min(body.time), body.speed, date.fromisoformat(body.date) if body.date else None)
    rt.bus.publish("clock.set", body.model_dump(), source="sim")
    return {"ok": True}


class CalendarSync(BaseModel):
    user: str = "vibhu"
    events: list[dict]  # same shape the iPhone sends; see calendar_sync.parse_events


@app.post("/sim/calendar")
async def sim_calendar(body: CalendarSync, request: Request):
    """What the iPhone does on connect and on every calendar change."""
    rt_of(request).devices.handle(body.user, {"type": "calendar", "events": body.events})
    return {"ok": True}


@app.post("/sim/calendar/demo")
async def sim_calendar_demo(request: Request, user: str = "vibhu"):
    """A demo week: a meeting happening now, and a Friday study group at 6:30pm."""
    rt = rt_of(request)
    today = rt.clock.today()
    now = int(rt.clock.now_min())
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    events = [
        {"id": "demo-standup", "title": "Team standup", "date": today.isoformat(),
         "start": f"{now // 60:02d}:{now % 60:02d}", "end": f"{min(now + 30, 1439) // 60:02d}:{min(now + 30, 1439) % 60:02d}"},
        {"id": "demo-study", "title": "Study group", "date": friday.isoformat(), "start": "18:30", "end": "20:00",
         "location": "McKeldin Library"},
    ]
    rt.devices.handle(user, {"type": "calendar", "events": events})
    return {"ok": True, "events": events}


@app.post("/sim/receipt")
async def sim_receipt(request: Request, user: str = "vibhu"):
    return {"spoken": await rt_of(request).timekeeper.send_receipt(user)}


# ---------- telephony ----------

async def twilio_verified(request: Request) -> dict[str, str]:
    """Form params of a Twilio webhook, rejected unless Twilio signed it."""
    s = rt_of(request).settings
    params = {k: str(v) for k, v in (await request.form()).items()}
    if s.twilio_token and s.public_url:
        url = s.public_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        if not valid_twilio_signature(s.twilio_token, url, params, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(403, "bad Twilio signature")
    return params


@app.post("/twilio/voice/{user}")
async def twilio_voice(user: str, request: Request, to: str, other: str = "caller"):
    rt = rt_of(request)
    await twilio_verified(request)
    profile = rt.world.profiles.get(user)
    if profile is None:
        raise HTTPException(404)
    base = (rt.settings.public_url or f"https://{request.url.hostname}").rstrip("/")
    media_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/twilio/media"
    twiml = owner_leg_twiml(user, profile.name, other, to, media_url, f"{base}/twilio/whisper/{user}")
    return Response(twiml, media_type="application/xml")


@app.post("/twilio/whisper/{user}")
async def twilio_whisper(user: str, request: Request):
    rt = rt_of(request)
    await twilio_verified(request)
    profile = rt.world.profiles.get(user)
    return Response(whisper_twiml(profile.name if profile else "the caller"), media_type="application/xml")


@app.websocket("/twilio/media")
async def twilio_media(ws: WebSocket):
    rt = rt_of(ws)
    await ws.accept()
    bridge = MediaBridge(rt.bus, rt.telephony.transcriber)
    try:
        while await bridge.handle(await ws.receive_json()):
            pass
    except WebSocketDisconnect:
        pass
    finally:
        await bridge.finish()
