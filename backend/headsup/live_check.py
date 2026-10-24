"""One live call per model-backed step, to check the prompts and schemas
against the real API before a demo.

    ANTHROPIC_API_KEY=... python -m headsup.live_check

Each check prints PASS or FAIL. A FAIL means the step fell back to its
offline heuristic, and the reason is printed.
"""

from __future__ import annotations

import asyncio
import sys

from .agents.concierge import CONCIERGE_SYSTEM, Command
from .agents.context import EXTRACT_SYSTEM, Extracted, Finding
from .agents.thinkers import JUDGE_SYSTEM, PLANNER_SYSTEM, TRIAGE_SYSTEM, JudgeVerdict, PlanChangeDraft, TriageDecision
from .agents.timekeeper import RECEIPT_SYSTEM, Receipt
from .brain import Brain
from .config import Settings
from .doctor import DOCTOR_SYSTEM, Diagnosis
from .events import EventBus, EventLog
from .ledger import Ledger


async def main() -> int:
    settings = Settings(db_path=":memory:")
    if settings.offline:
        print("No ANTHROPIC_API_KEY (or HEADSUP_OFFLINE is set); nothing to check.")
        return 2
    bus = EventBus(EventLog(":memory:"))
    errors = bus.subscribe("brain.error")
    ledger = Ledger(settings.daily_budget_usd)
    brain = Brain(settings, ledger, bus)

    sentinel = object()
    checks = [
        ("triage", lambda: brain.decide(TriageDecision, system=TRIAGE_SYSTEM, tier="fast", offline=lambda: sentinel,
                                        prompt="Flight UA1234 now delayed 90 min, landing 7:00pm. Plans: Dinner with Sam at 7:00pm (35 min away)")),
        ("planner", lambda: brain.decide(PlanChangeDraft, system=PLANNER_SYSTEM, tier="main", offline=lambda: sentinel,
                                         prompt="Vibhu will be late for 'Dinner with Sam' at Little Serow, 7:00pm. Proposed new time 7:45pm.")),
        ("extractor", lambda: brain.decide(Extracted, system=EXTRACT_SYSTEM, tier="fast", offline=lambda: sentinel,
                                           prompt="Owner: vibhu. Other caller: Sam.\nvibhu: Let's do Thai Chef near Dupont, Friday at 7.\n"
                                                  "Sam: Perfect. Heads Up, send me the address.\nvibhu: I'll bring the board game.")),
        ("judge", lambda: brain.decide(JudgeVerdict, system=JUDGE_SYSTEM, tier="fast", offline=lambda: sentinel,
                                       prompt="Plan: dinner at Thai Chef Friday 19:00 with Sam. Findings: closed Friday for a private event; "
                                              "Little Serow has a 7pm table.")),
        ("doctor", lambda: brain.decide(Diagnosis, system=DOCTOR_SYSTEM, tier="main", offline=lambda: sentinel,
                                        prompt="Problem: blocked restaurant_no_answer\nCandidates: ['retry', 'replan', 'escalate', 'safe_stop']\n"
                                               "Recent events: [('task.action', {'tool': 'phone_restaurant'}), ('task.blocked', {'reason': 'restaurant_no_answer'})]")),
        ("concierge", lambda: brain.decide(Command, system=CONCIERGE_SYSTEM, tier="fast", offline=lambda: sentinel,
                                           prompt="remember I parked on level 3, row F")),
        ("receipt", lambda: brain.decide(Receipt, system=RECEIPT_SYSTEM, tier="fast", offline=lambda: sentinel,
                                         prompt="Today's activity: {'handled': ['Move Dinner with Sam to about 7:45pm'], "
                                                "'fixed_by_itself': 2, 'handed_back': [], 'spent_usd': 0.06}")),
        ("researcher (web search)", lambda: brain.research(Finding, offline=lambda: sentinel,
                                                          question="Is Little Serow in Washington DC open on Friday evenings?")),
    ]

    failed = 0
    for name, run in checks:
        result = await run()
        if result is sentinel:
            failed += 1
            reason = errors.queue.get_nowait().data["error"] if not errors.queue.empty() else "unknown"
            print(f"FAIL  {name}: {reason}")
        else:
            print(f"PASS  {name}: {result.model_dump()}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed, spent ${ledger.total_usd:.4f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
