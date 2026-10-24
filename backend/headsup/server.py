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
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .chaos import FAULTS
from .config import settings
from .engine import process
from .lifecycle import LifecycleError
from .messaging import outbox
from .models import (
    ActionResult,
    ExecutionOutcome,
    Health,
    LocationInput,
    LocationResult,
    OutboxEntry,
    Plan,
    PlanCommand,
    PlanPatch,
    SessionState,
    Signal,
    SignalResult,
)
from .runtime import Runtime
from .voice import VoiceTools, handle_webhook
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Pairing-Token"],
)


def rt_of(request_or_ws) -> Runtime:
    return request_or_ws.app.state.rt


# ---------- stable application API ----------

@app.get("/api/health", response_model=Health)
async def api_health(request: Request):
    rt = rt_of(request)
    calendar = rt.calendar.health()
    messaging = rt.messaging.health()
    healthy = calendar.get("status") in {"ok", "configured"} and messaging.get("status") == "ok"
    return Health(
        status="ok" if healthy else "degraded",
        providers={
            "calendar": calendar,
            "messaging": messaging,
            "models": {"mode": "offline" if rt.settings.offline else "live", "status": "ok"},
            "phone_pairing": {"mode": "live", "status": rt.pairing.status()},
            "voice": {"mode": "live", "status": "configured" if rt.settings.vapi_webhook_secret else "disabled"},
        },
    )


@app.get("/api/session", response_model=SessionState)
async def api_session(request: Request):
    return rt_of(request).foundation.session()


class DemoReset(BaseModel):
    scenario_id: str = "foundation"


@app.post("/api/demo/reset", response_model=SessionState)
async def api_demo_reset(body: DemoReset, request: Request):
    try:
        return rt_of(request).reset_foundation(body.scenario_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/signals", response_model=SignalResult)
async def api_signals(body: Signal, request: Request):
    return rt_of(request).foundation.ingest_signal(body, process)


# Requests relayed by a tunnel or proxy carry these; a pairing must start on this machine.
FORWARDED_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip")


def from_this_machine(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"} and not any(h in request.headers for h in FORWARDED_HEADERS)


class PairingResult(BaseModel):
    token: str
    expires_at: datetime


@app.post("/api/pairing", response_model=PairingResult)
async def api_pairing(request: Request):
    if not from_this_machine(request):
        raise HTTPException(403, detail={"code": "LOCAL_ONLY", "message": "pair from the desktop screen"})
    token, expires_at = rt_of(request).pairing.mint()
    return PairingResult(token=token, expires_at=expires_at)


@app.post("/api/location", response_model=LocationResult)
async def api_location(
    body: LocationInput,
    request: Request,
    pairing_token: str | None = Header(default=None, alias="X-Pairing-Token"),
):
    rt = rt_of(request)
    if not rt.pairing.valid(pairing_token):
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "valid pairing token required"})
    age = (datetime.now(timezone.utc) - body.captured_at).total_seconds()
    if age > 120 or age < -30:
        return LocationResult(status="stale", error_code="STALE_LOCATION")
    fingerprint = f"{body.latitude:.6f}:{body.longitude:.6f}:{body.captured_at.isoformat()}"
    signal = Signal(
        id="location_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:12],
        kind="location_eta",
        source="phone",
        source_mode="live",
        observed_at=body.captured_at,
        position={
            "latitude": body.latitude,
            "longitude": body.longitude,
            "accuracy_meters": body.accuracy_meters,
        },
        route_duration_seconds=body.route_duration_seconds,
    )
    uncertain = body.accuracy_meters > 200
    result = rt.foundation.ingest_signal(signal, None if uncertain else process)
    return LocationResult(status="uncertain" if uncertain else "accepted", signal=result)


def lifecycle_error(exc: LifecycleError) -> HTTPException:
    status = 404 if exc.code == "NOT_FOUND" else 409
    return HTTPException(status, detail={"code": exc.code, "message": str(exc)})


@app.get("/api/plans/{plan_id}", response_model=Plan)
async def api_plan(plan_id: str, request: Request):
    try:
        return rt_of(request).lifecycle.get_plan(plan_id)
    except LifecycleError as exc:
        raise lifecycle_error(exc) from exc


@app.patch("/api/plans/{plan_id}", response_model=Plan)
async def api_plan_patch(plan_id: str, body: PlanPatch, request: Request):
    try:
        return rt_of(request).lifecycle.revise(plan_id, body)
    except LifecycleError as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/plans/{plan_id}/approve", response_model=ExecutionOutcome)
async def api_plan_approve(plan_id: str, body: PlanCommand, request: Request):
    rt = rt_of(request)
    try:
        queued = rt.lifecycle.approve(plan_id, body.expected_version, body.user_id, body.channel)
    except LifecycleError as exc:
        raise lifecycle_error(exc) from exc
    # The approval is already durable; if this request dies here, the executor resumes on restart.
    plan = await rt.executor.run_plan(plan_id)
    results = rt.foundation.results_for_actions([action.id for action in plan.actions])
    return ExecutionOutcome(plan=plan, results=results, duplicate=queued.duplicate)


@app.post("/api/plans/{plan_id}/reject", response_model=Plan)
async def api_plan_reject(plan_id: str, body: PlanCommand, request: Request):
    try:
        return rt_of(request).lifecycle.reject(plan_id, body.expected_version)
    except LifecycleError as exc:
        raise lifecycle_error(exc) from exc


@app.post("/api/voice/webhook")
async def api_voice_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    x_vapi_secret: str | None = Header(default=None),
):
    rt = rt_of(request)
    expected = rt.settings.vapi_webhook_secret
    if not expected:
        raise HTTPException(503, detail={"code": "VOICE_DISABLED", "message": "VAPI_WEBHOOK_SECRET is not set"})
    supplied = x_vapi_secret or (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "valid Vapi credential required"})
    tools = VoiceTools(rt.lifecycle, rt.foundation.session, rt.executor.run_plan)
    return await handle_webhook(tools, await request.json())


@app.get("/api/actions", response_model=list[ActionResult])
async def api_actions(request: Request):
    return rt_of(request).foundation.session().results


@app.get("/api/outbox", response_model=list[OutboxEntry])
async def api_outbox(request: Request):
    return outbox(rt_of(request).foundation)


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
