"""Hold the line for me.

"Hey Heads Up, call United and get me rebooked." Heads Up dials the business,
works through the phone menu, waits on hold, and when a person picks up it
introduces itself as an automated assistant, taps the user's wrist and asks
them to nod to join. A nod conferences the user in.

Everything the business says arrives as `hold.heard` events, from either the
simulated business or a real Twilio call (via the media stream). The
HoldLineRunner turns those lines into steps the Supervisor can watch: a
phone menu that keeps repeating is a loop, a hold that never ends is a
question for the user, and the user not answering ends the call politely.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Literal
from xml.sax.saxutils import escape, quoteattr

import httpx
from pydantic import BaseModel

from .agents.workers import TaskRunner
from .chaos import Chaos
from .config import Settings
from .contracts import Task
from .events import EventBus

# ---------- understanding what the line says ----------

HOLD_PHRASES = [
    "please hold", "continue to hold", "your call is important", "remain on the line", "stay on the line",
    "estimated wait", "next available", "all of our agents", "high call volume", "thank you for your patience",
]
HUMAN_PATTERNS = [
    re.compile(r"\bthis is [A-Z][a-z]+"),  # case matters: a name, not "this is a recording"
    re.compile(r"\bmy name is\b", re.IGNORECASE),
    re.compile(r"\bhow (can|may) I (help|assist)", re.IGNORECASE),
    re.compile(r"\bwho am I speaking", re.IGNORECASE),
]
MENU_PATTERN = r"\bpress (\d|star|pound|zero)\b|\bsay or press\b"

LineKindName = Literal["menu", "hold", "human", "other"]


class LineKind(BaseModel):
    kind: LineKindName


class IVRChoice(BaseModel):
    digit: str  # one key, "0"-"9", "*" or "#"
    reason: str


LINE_SYSTEM = (
    "Classify one line heard on a phone call to a business: 'menu' (an automated menu with options), 'hold' "
    "(a hold recording or music), 'human' (a live person greeting the caller), or 'other'."
)
IVR_SYSTEM = (
    "You are navigating a phone menu for a caller. Given the menu options and the caller's goal, pick the one "
    "key most likely to reach a person who can help. If nothing fits, pick 0 for an operator."
)


def classify_heuristic(text: str, seen: set[str]) -> LineKindName:
    t = text.lower()
    if re.search(MENU_PATTERN, t):
        return "menu"
    if any(p in t for p in HOLD_PHRASES):
        return "hold"
    if any(p.search(text) for p in HUMAN_PATTERNS):
        return "human"
    if t in seen:
        return "hold"  # a line that repeats is a recording
    return "other"


def parse_menu(text: str) -> list[tuple[str, str]]:
    """[("2", "changes to an existing trip"), ...] from a spoken menu."""
    options: list[tuple[str, str]] = []
    for m in re.finditer(r"(?:for|to) ([^.,;]+?),? press (\d)", text, re.IGNORECASE):
        options.append((m.group(2), m.group(1).strip()))
    for m in re.finditer(r"press (\d),? (?:for|to) ([^.,;]+)", text, re.IGNORECASE):
        options.append((m.group(1), m.group(2).strip()))
    return options


SYNONYMS = {
    "rebook": {"change", "changes", "existing", "trip", "reservation", "flight"},
    "rebooked": {"change", "changes", "existing", "trip", "reservation", "flight"},
    "refund": {"refund", "refunds", "billing", "payment"},
    "bag": {"baggage", "bags", "luggage"},
    "cancel": {"cancel", "cancellation", "existing", "change"},
}


def choose_heuristic(options: list[tuple[str, str]], purpose: str) -> IVRChoice:
    words = set(re.findall(r"[a-z]+", purpose.lower()))
    for w in list(words):
        words |= SYNONYMS.get(w, set())
    best = max(options, key=lambda o: len(words & set(re.findall(r"[a-z]+", o[1].lower()))), default=None)
    if best and words & set(re.findall(r"[a-z]+", best[1].lower())):
        return IVRChoice(digit=best[0], reason=f"'{best[1]}' matches the goal")
    return IVRChoice(digit="0", reason="no option fits; asking for an operator")


# ---------- drivers: the simulated business and real Twilio calls ----------

@dataclass
class SimBusiness:
    """A business phone line with a menu, a hold queue and an agent. Chaos can
    make the menu loop (`ivr_loop`) or the hold never end (`hold_forever`)."""

    bus: EventBus
    chaos: Chaos
    segment_s: float = 1.5  # time between hold recordings
    hold_segments: int = 4  # recordings before an agent picks up
    correct_digit: str = "2"
    log: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    _keys: dict[str, asyncio.Queue] = field(default_factory=dict)
    _scripts: dict[str, asyncio.Task] = field(default_factory=dict)

    def _hear(self, task: Task, text: str) -> None:
        self.bus.publish("hold.heard", {"text": text, "business": task.context["business"]},
                         source="business", user=task.user, task_id=task.id)

    async def dial(self, task: Task) -> bool:
        self.log[task.id] = [("dialed", task.context["business"])]
        self._keys[task.id] = asyncio.Queue()
        self._scripts[task.id] = asyncio.create_task(self._script(task))
        return True

    async def _script(self, task: Task) -> None:
        name = task.context["business"]
        seg, keys = self.segment_s, self._keys[task.id]
        await asyncio.sleep(seg / 2)
        self._hear(task, f"Thank you for calling {name}.")
        while True:  # the menu
            await asyncio.sleep(seg / 2)
            self._hear(task, "For new reservations, press 1. For changes to an existing trip, press 2. "
                             "For baggage, press 3. To speak with an agent, press 0.")
            try:
                digit = await asyncio.wait_for(keys.get(), timeout=seg * 4)
            except asyncio.TimeoutError:
                continue
            if digit == "0" or (digit == self.correct_digit and not self.chaos.on("ivr_loop")):
                break
            if digit != self.correct_digit:
                self._hear(task, "Sorry, I didn't get that.")
        self._hear(task, "Please hold. Your call is important to us.")
        i = 0
        while self.chaos.on("hold_forever") or i < self.hold_segments:
            await asyncio.sleep(seg)
            self._hear(task, "Thank you for your patience. Your call will be answered by the next available agent.")
            i += 1
        await asyncio.sleep(seg)
        self._hear(task, f"Thanks for holding, this is Maria with {name}. How can I help you today?")

    async def press(self, task: Task, digit: str) -> None:
        self.log[task.id].append(("pressed", digit))
        await self._keys[task.id].put(digit)

    async def say(self, task: Task, text: str) -> None:
        self.log[task.id].append(("said", text))

    async def bridge(self, task: Task, user_phone: str) -> bool:
        self.log[task.id].append(("bridged", user_phone))
        return True

    async def hangup(self, task: Task, goodbye: str | None = None) -> None:
        if goodbye:
            await self.say(task, goodbye)
        self.log[task.id].append(("hung_up", ""))
        script = self._scripts.pop(task.id, None)
        if script:
            script.cancel()


class TwilioHoldLine:
    """Real calls. The business call forks its audio to /twilio/media, whose
    transcripts come back as `hold.heard`. Keys, speech and the conference are
    sent by updating the live call's TwiML.

    To verify on a real call: whether Twilio keeps a <Start><Stream> running
    across TwiML updates. This driver doesn't assume it does: the media bridge
    reports when a stream starts and stops, and an update re-adds the stream
    whenever none is running.
    """

    HOLD_PAUSE = '<Pause length="14400"/>'

    def __init__(self, settings: Settings, bus: EventBus):
        self.s = settings
        self.bus = bus
        self.streaming: set[str] = set()
        self.calls: dict[str, str] = {}  # task id -> call sid
        self._sub = bus.subscribe("hold.stream")  # the media bridge reports streams starting and stopping

    def _drain(self) -> None:
        while not self._sub.queue.empty():
            e = self._sub.queue.get_nowait()
            (self.streaming.add if e.data["up"] else self.streaming.discard)(e.task_id)

    def _stream(self, task: Task) -> str:
        self._drain()
        if task.id in self.streaming:
            return ""
        url = self.s.public_url.rstrip("/").replace("https://", "wss://") + "/twilio/media"
        params = {"mode": "hold", "task_id": task.id, "owner": task.user, "other": task.context["business"]}
        inner = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in params.items())
        return f"<Start><Stream url={quoteattr(url)} track=\"inbound_track\">{inner}</Stream></Start>"

    async def _api(self, path: str, data: dict) -> dict:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.s.twilio_sid}/{path}"
        async with httpx.AsyncClient(timeout=10, auth=(self.s.twilio_sid, self.s.twilio_token)) as http:
            r = await http.post(url, data=data)
        if r.status_code >= 300:
            raise RuntimeError(f"Twilio {path}: HTTP {r.status_code}")
        return r.json()

    async def _update(self, task: Task, body: str) -> None:
        sid = self.calls[task.id]
        await self._api(f"Calls/{sid}.json", {"Twiml": f"<Response>{self._stream(task)}{body}</Response>"})

    async def dial(self, task: Task) -> bool:
        twiml = f"<Response>{self._stream(task)}{self.HOLD_PAUSE}</Response>"
        try:
            call = await self._api("Calls.json", {"To": task.context["phone"], "From": self.s.twilio_from,
                                                  "Twiml": twiml})
        except (RuntimeError, httpx.HTTPError):
            return False
        self.calls[task.id] = call["sid"]
        task.context["call_sid"] = call["sid"]
        return True

    async def press(self, task: Task, digit: str) -> None:
        await self._update(task, f'<Play digits="w{escape(digit)}"/>{self.HOLD_PAUSE}')

    async def say(self, task: Task, text: str) -> None:
        await self._update(task, f"<Say>{escape(text)}</Say>{self.HOLD_PAUSE}")

    async def bridge(self, task: Task, user_phone: str) -> bool:
        room = f"hold-{task.id}"
        conference = f'<Dial><Conference endConferenceOnExit="true">{room}</Conference></Dial>'
        try:
            await self._update(task, conference)
            await self._api("Calls.json", {"To": user_phone, "From": self.s.twilio_from, "Twiml":
                            f"<Response><Say>Connecting you to {escape(task.context['business'])}.</Say>"
                            f"{conference}</Response>"})
        except (RuntimeError, httpx.HTTPError):
            return False
        return True

    async def hangup(self, task: Task, goodbye: str | None = None) -> None:
        if task.id not in self.calls:
            return
        say = f"<Say>{escape(goodbye)}</Say>" if goodbye else ""
        try:
            await self._api(f"Calls/{self.calls.pop(task.id)}.json", {"Twiml": f"<Response>{say}<Hangup/></Response>"})
        except (RuntimeError, httpx.HTTPError):
            pass


# ---------- the task ----------

class HoldLineRunner(TaskRunner):
    def __init__(self, rt, task: Task):
        super().__init__(rt, task)
        self.steps = {
            "dialed": self.dial,
            "through_menu": self.through_menu,
            "human_on_line": self.wait_for_human,
            "connected": self.connect,
        }
        self.sub = rt.bus.subscribe("hold.heard")
        self.seen: set[str] = set()

    @property
    def driver(self):
        return self.rt.holdline

    async def next_line(self, timeout: float) -> str | None:
        deadline = time.time() + timeout
        while self.task.active and time.time() < deadline:
            try:
                e = await asyncio.wait_for(self.sub.get(), timeout=min(1.0, max(deadline - time.time(), 0.01)))
            except asyncio.TimeoutError:
                continue
            if e.task_id == self.task.id:
                return e.data["text"]
        return None

    async def classify(self, text: str) -> LineKindName:
        kind = classify_heuristic(text, self.seen)
        if kind == "other" and len(text.split()) > 4:  # only ambiguous lines cost a model call
            kind = (await self.rt.brain.decide(LineKind, system=LINE_SYSTEM, tier="fast", task_id=self.task.id,
                                               offline=lambda: LineKind(kind="other"), prompt=text)).kind
        self.seen.add(text.lower())
        self.rt.bus.publish("hold.classified", {"text": text, "kind": kind}, source="holdline",
                            user=self.task.user, task_id=self.task.id)
        return kind

    async def dial(self) -> bool:
        self.act("dial_business", {"business": self.task.context["business"]})
        return await self.driver.dial(self.task) or self.block("dial_failed")

    async def through_menu(self) -> bool:
        t = self.task
        presses = 0
        while t.active:
            if t.context.get("frozen"):
                return self.block("frozen by supervisor")
            text = await self.next_line(timeout=30)
            if text is None:
                return self.block("line_silent")
            kind = await self.classify(text)
            if kind == "menu":
                options = parse_menu(text)
                if t.strategy == "operator" or not options:
                    choice = IVRChoice(digit="0", reason="asking for an operator")
                else:
                    choice = await self.rt.brain.decide(
                        IVRChoice, system=IVR_SYSTEM, tier="fast", task_id=t.id,
                        offline=lambda: choose_heuristic(options, t.context["purpose"]),
                        prompt=f"Goal: {t.context['purpose']}\nMenu: {options}")
                    if choice.digit not in {d for d, _ in options} | {"0"}:
                        choice = IVRChoice(digit="0", reason="model picked a key not on the menu")
                self.act("send_dtmf", {"digit": choice.digit})
                await self.driver.press(t, choice.digit)
                presses += 1
                if presses > 8:
                    return self.block("ivr_stuck")
            elif kind == "hold":
                t.context["holding_since"] = time.time()
                return True
            elif kind == "human":
                t.context["human"] = True
                return True
        return False

    async def wait_for_human(self) -> bool:
        t = self.task
        if t.context.get("human"):
            return True
        limit = t.context["hold_limit_s"]
        start = t.context.get("holding_since", time.time())
        while t.active:
            text = await self.next_line(timeout=min(5.0, limit))
            if text and await self.classify(text) == "human":
                t.context["human"] = True
                return True
            if time.time() - start > limit:
                minutes = max(1, round((time.time() - t.context.get("holding_since", start)) / 60))
                t.context["question"] = f"Still on hold with {t.context['business']} after {minutes} minutes. Keep waiting?"
                if not await self.approve():
                    return False  # "no" ends the task; no answer leaves it blocked for the Doctor
                start = time.time()
        return False

    async def connect(self) -> bool:
        t = self.task
        owner = self.rt.world.profiles[t.user]
        intro = (f"Hi, thanks for picking up. I'm Heads Up, an automated assistant calling for {owner.name}, "
                 f"and this call is transcribed. {owner.name} will join in just a moment.")
        self.act("speak_to_business", {"text": "intro"})
        await self.driver.say(t, intro)
        t.context["question"] = f"{t.context['business']} agent is on the line. Nod to join."
        if not await self.approve():
            return False
        self.act("conference_user", {"phone": owner.phone})
        if not await self.driver.bridge(t, owner.phone):
            return self.block("bridge_failed")
        t.context["connected"] = True
        self.rt.bus.publish("hold.connected", {"business": t.context["business"]}, source="holdline",
                            user=t.user, task_id=t.id)
        return True

    async def cleanup(self) -> None:
        self.rt.bus.unsubscribe(self.sub)
        if not self.task.context.get("connected"):
            goodbye = ("Sorry, they can't join right now. We'll call back. Goodbye."
                       if self.task.context.get("human") else None)
            await self.driver.hangup(self.task, goodbye)
