"""Concierge: handles what the user says after "Hey Heads Up"."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from .base import Agent


class Command(BaseModel):
    intent: Literal["remember", "recall", "call", "hold_line", "forget_call", "what_did_you_find", "receipt",
                    "other"]
    argument: str  # the thing to remember, the person or business to call, etc. Empty when none.
    purpose: str = ""  # hold_line only: what the user needs from the business


CONCIERGE_SYSTEM = (
    "Classify a short voice command for Heads Up, a voice assistant. 'remember' stores a fact, 'recall' asks for "
    "one back, 'call' places a phone call to a person, 'hold_line' calls a business, waits on hold and connects "
    "the user when a person answers (argument = the business, purpose = what they need), 'forget_call' deletes "
    "what was learned from the last call, 'what_did_you_find' asks for research results, 'receipt' asks what "
    "Heads Up did today."
)

BUSINESS_HINTS = r"(?i)\b(airline|airlines|united|delta|american|pharmacy|cvs|comcast|bank|insurance|support)\b"


def heuristic(text: str) -> Command:
    t = text.strip()
    if m := re.match(r"(?i)remember (?:that )?(.+)", t):
        return Command(intent="remember", argument=m.group(1))
    if m := re.match(r"(?i)(?:call|get me through to|hold for) (.+?)(?: and (.+?))?[.?!]?$", t):
        who, purpose = m.group(1), m.group(2) or ""
        if purpose or re.search(BUSINESS_HINTS, who):
            return Command(intent="hold_line", argument=who, purpose=purpose or "speak to someone")
        return Command(intent="call", argument=who)
    if re.search(r"(?i)forget (that|the) call", t):
        return Command(intent="forget_call", argument="")
    if re.search(r"(?i)what did you find", t):
        return Command(intent="what_did_you_find", argument="")
    if re.search(r"(?i)what did you do|how was (my|the) day|today'?s receipt", t):
        return Command(intent="receipt", argument="")
    if m := re.search(r"(?i)where (?:did i|is) (.+)", t):
        return Command(intent="recall", argument=m.group(1))
    return Command(intent="other", argument=t)


class Concierge(Agent):
    name = "concierge"

    async def run(self) -> None:
        sub = self.rt.bus.subscribe("user.wake")
        while True:
            e = await self.next(sub)
            await self.handle(e.user, e.data.get("transcript", ""))

    async def handle(self, user: str, text: str) -> None:
        rt = self.rt
        cmd = await rt.brain.decide(Command, system=CONCIERGE_SYSTEM, tier="fast", offline=lambda: heuristic(text),
                                    prompt=text)
        rt.bus.publish("concierge.command", cmd.model_dump(), source=self.name, user=user)
        memory = rt.world.memory.setdefault(user, [])
        if cmd.intent == "remember":
            memory.append(cmd.argument)
            await rt.devices.haptic(user, "handled")
        elif cmd.intent == "recall":
            stems = {w[:4] for w in re.findall(r"[a-z]+", cmd.argument.lower()) if len(w) > 3}
            hits = [m for m in memory if stems & {w[:4] for w in re.findall(r"[a-z]+", m.lower())}]
            await rt.devices.say(user, hits[-1] if hits else "I don't have anything on that.")
        elif cmd.intent == "call":
            result = await rt.telephony.call(user, cmd.argument)
            await rt.devices.say(user, f"Calling {result['name']}. Your phone will ring." if result["ok"]
                                 else result["reason"])
        elif cmd.intent == "hold_line":
            found = rt.world.business(cmd.argument)
            if found is None:
                await rt.devices.say(user, f"I don't have a number for {cmd.argument}.")
                return
            name, phone = found
            rt.planner.hold_line(user, name, phone, cmd.purpose or "speak to someone")
            await rt.devices.say(user, f"Calling {name}. I'll wait on hold and tap you when a person picks up.")
        elif cmd.intent == "receipt":
            await rt.timekeeper.send_receipt(user)
        elif cmd.intent == "forget_call":
            rt.world.commitments[user].clear()
            rt.bus.publish("privacy.forgot_call", {}, source=self.name, user=user)
            await rt.devices.say(user, "Done. I've deleted everything from that call.")
        elif cmd.intent == "what_did_you_find":
            found = [e["data"] for e in rt.log.recent(100) if e["kind"] == "research.finding" and e["user"] == user]
            await rt.devices.say(user, f"{found[-1]['place']}: {found[-1]['notes'] or 'all good'}." if found
                                 else "Nothing new.")
        else:
            await rt.devices.say(user, "Sorry, I didn't catch that.")
