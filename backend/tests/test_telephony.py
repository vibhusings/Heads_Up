"""Calls through Heads Up: TwiML, webhook signatures, and the media stream to transcripts."""

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from headsup import server
from headsup.telephony import owner_leg_twiml, valid_twilio_signature


class FakeTranscriber:
    """Treats each audio frame's bytes as already-transcribed text."""

    async def start(self, on_line):
        self.on_line = on_line

    async def feed(self, mulaw: bytes):
        await self.on_line(mulaw.decode())

    async def close(self):
        pass


def sign(token: str, url: str, params: dict) -> str:
    payload = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def test_owner_leg_twiml_has_notice_fork_and_dial():
    xml = owner_leg_twiml("vibhu", "Vibhu", "Sam", "+15550000002", "wss://x/twilio/media", "https://x/twilio/whisper/vibhu")
    assert "<Say>This call has Heads Up assisting Vibhu.</Say>" in xml
    assert 'track="both_tracks"' in xml and '<Parameter name="other" value="Sam"/>' in xml
    assert '<Number url="https://x/twilio/whisper/vibhu">+15550000002</Number>' in xml


def test_signature_check_accepts_signed_and_rejects_tampered():
    params = {"CallSid": "CA123", "From": "+15550000001"}
    url = "https://example.ngrok.app/twilio/voice/vibhu?to=%2B15550000002&other=Sam"
    sig = sign("secret", url, params)
    assert valid_twilio_signature("secret", url, params, sig)
    assert not valid_twilio_signature("secret", url, params | {"From": "+19999999999"}, sig)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.settings, "db_path", ":memory:")
    monkeypatch.setattr(server.settings, "offline", True)
    monkeypatch.setattr(server.settings, "twilio_token", "secret")
    monkeypatch.setattr(server.settings, "public_url", "https://example.ngrok.app")
    with TestClient(server.app) as c:
        c.app.state.rt.telephony.transcriber = FakeTranscriber
        yield c


def test_voice_webhook_requires_twilio_signature(client):
    path = "/twilio/voice/vibhu?to=%2B15550000002&other=Sam"
    assert client.post(path, data={"CallSid": "CA1"}).status_code == 403
    sig = sign("secret", "https://example.ngrok.app" + path, {"CallSid": "CA1"})
    r = client.post(path, data={"CallSid": "CA1"}, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "wss://example.ngrok.app/twilio/media" in r.text


def test_media_stream_becomes_a_labeled_transcript(client):
    def frame(track, text):
        return {"event": "media", "media": {"track": track, "payload": base64.b64encode(text.encode()).decode()}}

    with client.websocket_connect("/twilio/media") as ws:
        ws.send_json({"event": "start", "start": {"streamSid": "MZ1", "customParameters": {"owner": "vibhu", "other": "Sam"}}})
        ws.send_json(frame("inbound", "Let's do Thai Chef near Dupont, Friday at 7."))
        ws.send_json(frame("outbound", "Perfect, see you then."))
        ws.send_json({"event": "stop"})

    rt = client.app.state.rt
    ended = next(e for e in rt.log.recent(200) if e["kind"] == "call.ended")
    assert ended["user"] == "vibhu"
    assert [line["speaker"] for line in ended["data"]["transcript"]] == ["vibhu", "Sam"]


async def test_call_without_twilio_says_so(rt):
    result = await rt.telephony.call("vibhu", "Sam")
    assert not result["ok"] and "isn't set up" in result["reason"]
    assert (await rt.telephony.call("vibhu", "Nobody"))["reason"].startswith("I don't have a number")
