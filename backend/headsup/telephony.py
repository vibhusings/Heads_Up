"""Calls placed through Heads Up (Twilio), transcribed live.

Flow for "Hey Heads Up, call Sam":
  1. `Telephony.call` asks Twilio to ring the owner's phone.
  2. When the owner answers, Twilio fetches /twilio/voice: a consent notice,
     an audio fork of both directions to /twilio/media, then it dials Sam.
     Sam hears the same notice from /twilio/whisper before being connected.
  3. `MediaBridge` sends each direction to its own speech-to-text stream, so
     every line is labeled with who said it.
  4. Hanging up publishes `call.ended` for the Extractor. Raw audio is never stored.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from .config import Settings
from .events import EventBus
from .world import World

OnLine = Callable[[str], Awaitable[None]]


# ---------- TwiML ----------

def consent_notice(owner_name: str) -> str:
    return f"This call has Heads Up assisting {owner_name}."


def owner_leg_twiml(owner: str, owner_name: str, other_name: str, other_number: str,
                    media_ws_url: str, whisper_url: str) -> str:
    """Played to the owner on answer: notice, fork both audio tracks, dial the other person."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"<Say>{escape(consent_notice(owner_name))}</Say>"
        f"<Start><Stream url={quoteattr(media_ws_url)} track=\"both_tracks\">"
        f"<Parameter name=\"owner\" value={quoteattr(owner)}/>"
        f"<Parameter name=\"other\" value={quoteattr(other_name)}/>"
        "</Stream></Start>"
        f"<Dial><Number url={quoteattr(whisper_url)}>{escape(other_number)}</Number></Dial>"
        "</Response>"
    )


def whisper_twiml(owner_name: str) -> str:
    """Played to the other person before they are connected."""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{escape(consent_notice(owner_name))}</Say></Response>'


def valid_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Twilio signs each webhook: HMAC-SHA1 over the full URL plus sorted POST params."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


# ---------- speech-to-text ----------

class Transcriber(Protocol):
    async def start(self, on_line: OnLine) -> None: ...
    async def feed(self, mulaw: bytes) -> None: ...
    async def close(self) -> None: ...


class NullTranscriber:
    """No speech-to-text configured: the call still connects, nothing is transcribed."""

    async def start(self, on_line: OnLine) -> None:
        pass

    async def feed(self, mulaw: bytes) -> None:
        pass

    async def close(self) -> None:
        pass


class DeepgramTranscriber:
    """Streaming transcription of 8 kHz mu-law audio, the format Twilio sends."""

    URL = ("wss://api.deepgram.com/v1/listen?model=nova-3&encoding=mulaw&sample_rate=8000&channels=1"
           "&punctuate=true&smart_format=true&endpointing=300")

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._ws = None
        self._reader: asyncio.Task | None = None

    async def start(self, on_line: OnLine) -> None:
        self._ws = await ws_connect(self.URL, additional_headers={"Authorization": f"Token {self.api_key}"})
        self._reader = asyncio.create_task(self._read(on_line))

    async def _read(self, on_line: OnLine) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("type") != "Results" or not msg.get("is_final"):
                    continue
                text = msg["channel"]["alternatives"][0]["transcript"].strip()
                if text:
                    await on_line(text)
        except WebSocketException:
            pass

    async def feed(self, mulaw: bytes) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(mulaw)
            except WebSocketException:
                self._ws = None

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                if self._reader:
                    await asyncio.wait_for(self._reader, timeout=3)  # let the last results arrive
            except (WebSocketException, asyncio.TimeoutError):
                pass
            await self._ws.close()


# ---------- the call itself ----------

@dataclass
class CallSession:
    bus: EventBus
    owner: str
    other: str
    lines: list[dict[str, str]] = field(default_factory=list)

    def add_line(self, speaker: str, text: str) -> None:
        line = {"speaker": speaker, "text": text}
        self.lines.append(line)
        self.bus.publish("call.transcript", line, source="telephony", user=self.owner)

    def end(self) -> None:
        self.bus.publish("call.ended", {"other": self.other, "transcript": self.lines},
                         source="telephony", user=self.owner)


class MediaBridge:
    """Handles one Twilio media stream. On a call Twilio placed, `inbound` is the
    person Twilio dialed and `outbound` is what they hear.

    Two kinds of stream: a call with a person (the owner's leg; both sides are
    transcribed and the transcript goes to the Extractor), and a hold-the-line
    call to a business (mode=hold; only the business is transcribed, line by
    line, as `hold.heard` for its task)."""

    def __init__(self, bus: EventBus, make_transcriber: Callable[[], Transcriber]):
        self.bus = bus
        self.make_transcriber = make_transcriber
        self.call: CallSession | None = None
        self.hold: dict[str, str] | None = None  # custom parameters of a hold call
        self.tracks: dict[str, Transcriber] = {}

    async def handle(self, msg: dict) -> bool:
        """Process one Twilio message. Returns False when the stream is over."""
        event = msg.get("event")
        if event == "start" and msg["start"].get("customParameters", {}).get("mode") == "hold":
            self.hold = msg["start"]["customParameters"]
            self.bus.publish("hold.stream", {"up": True}, source="telephony", user=self.hold.get("owner"),
                             task_id=self.hold["task_id"])
            transcriber = self.make_transcriber()
            await transcriber.start(self._hold_writer())
            self.tracks["inbound"] = transcriber
        elif event == "start":
            params = msg["start"].get("customParameters", {})
            self.call = CallSession(self.bus, params.get("owner", "unknown"), params.get("other", "caller"))
            self.bus.publish("call.started", {"other": self.call.other, "stream": msg["start"].get("streamSid")},
                             source="telephony", user=self.call.owner)
            for track, speaker in (("inbound", self.call.owner), ("outbound", self.call.other)):
                transcriber = self.make_transcriber()
                await transcriber.start(self._writer(speaker))
                self.tracks[track] = transcriber
        elif event == "media" and (self.call or self.hold):
            transcriber = self.tracks.get(msg["media"].get("track", "inbound"))
            if transcriber:
                await transcriber.feed(base64.b64decode(msg["media"]["payload"]))
        elif event == "stop":
            return False
        return True

    def _writer(self, speaker: str) -> OnLine:
        async def write(text: str) -> None:
            if self.call:
                self.call.add_line(speaker, text)
        return write

    def _hold_writer(self) -> OnLine:
        async def write(text: str) -> None:
            assert self.hold is not None
            self.bus.publish("hold.heard", {"text": text, "business": self.hold.get("other", "")},
                             source="telephony", user=self.hold.get("owner"), task_id=self.hold["task_id"])
        return write

    async def finish(self) -> None:
        for transcriber in self.tracks.values():
            await transcriber.close()
        if self.hold:
            self.bus.publish("hold.stream", {"up": False}, source="telephony", user=self.hold.get("owner"),
                             task_id=self.hold["task_id"])
        elif self.call:
            self.call.end()


class Telephony:
    def __init__(self, settings: Settings, bus: EventBus, world: World):
        self.settings = settings
        self.bus = bus
        self.world = world

    def transcriber(self) -> Transcriber:
        if self.settings.deepgram_key:
            return DeepgramTranscriber(self.settings.deepgram_key)
        return NullTranscriber()

    def lookup(self, who: str) -> tuple[str, str] | None:
        """(display name, phone number) for someone the owner asked to call."""
        name = who.strip().lower()
        for p in self.world.profiles.values():
            if p.name.lower() == name or p.user_id == name:
                return p.name, p.phone
        return None

    async def call(self, owner: str, who: str) -> dict:
        target = self.lookup(who)
        if target is None:
            return {"ok": False, "reason": f"I don't have a number for {who}."}
        name, number = target
        if not self.settings.twilio_ready:
            self.bus.publish("call.requested", {"who": name, "placed": False, "reason": "Twilio not configured"},
                             source="telephony", user=owner)
            return {"ok": False, "reason": "Calling isn't set up yet."}
        s = self.settings
        url = f"{s.public_url}/twilio/voice/{owner}?" + urlencode({"to": number, "other": name})
        async with httpx.AsyncClient(timeout=10, auth=(s.twilio_sid, s.twilio_token)) as http:
            r = await http.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_sid}/Calls.json",
                                data={"To": self.world.profiles[owner].phone, "From": s.twilio_from, "Url": url})
        if r.status_code >= 300:
            self.bus.publish("call.failed", {"who": name, "status": r.status_code}, source="telephony", user=owner)
            return {"ok": False, "reason": "The call didn't go through."}
        sid = r.json().get("sid")
        self.bus.publish("call.requested", {"who": name, "placed": True, "sid": sid}, source="telephony", user=owner)
        return {"ok": True, "sid": sid, "name": name}
