"""Fault injection for the demo. A teammate flips these from the judges' screen."""

from __future__ import annotations

from .events import EventBus

FAULTS = {
    "flight_api_down": "Primary flight data source returns errors",
    "restaurant_no_answer": "Restaurant phone line never picks up",
    "inject": "Restaurant asks for the card number on file",
    "loop": "Caller keeps redialing the same number",
    "cost_spike": "Every model call costs 25x more",
    "booking_down": "Online booking is down too (unfixable)",
    "ivr_loop": "Business phone menu keeps repeating",
    "hold_forever": "Business hold queue never ends",
}


class Chaos:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.flags: dict[str, bool] = {name: False for name in FAULTS}

    def on(self, name: str) -> bool:
        return self.flags.get(name, False)

    def set(self, name: str, value: bool) -> None:
        if name not in FAULTS:
            raise KeyError(name)
        self.flags[name] = value
        self.bus.publish("chaos.set", {"fault": name, "on": value, "what": FAULTS[name]}, source="chaos")
