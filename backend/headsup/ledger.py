"""Spend ledger: what every model call cost, per task and per day."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

# US dollars per million tokens (input, output).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


@dataclass
class Ledger:
    daily_budget_usd: float
    by_task: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    total_usd: float = 0.0
    started: float = field(default_factory=time.time)
    multiplier: float = 1.0  # chaos mode raises this to simulate a cost spike

    def charge(self, usd: float, task_id: str | None = None) -> float:
        usd *= self.multiplier
        self.total_usd += usd
        if task_id:
            self.by_task[task_id] += usd
        return usd

    def burn_rate_per_hour(self) -> float:
        hours = max((time.time() - self.started) / 3600, 0.25)  # don't extrapolate from the first minutes
        return self.total_usd / hours

    def projected_over(self, hours_left: float = 8.0) -> bool:
        return self.total_usd + self.burn_rate_per_hour() * hours_left > self.daily_budget_usd

    @property
    def remaining(self) -> float:
        return self.daily_budget_usd - self.total_usd
