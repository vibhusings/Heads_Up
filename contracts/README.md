# HeadsUp shared contracts

The canonical Python models are in `backend/headsup/models.py`; FastAPI publishes
their JSON schemas through `/openapi.json`. Frontend and engine code should use
these names and fields rather than creating parallel shapes.

Foundation endpoints:

- `GET /api/health` — enabled provider modes and status; never secrets.
- `GET /api/session` — contacts, events, signals, impacts, plans, actions, results, and approvals.
- `POST /api/demo/reset` with `{"scenario_id":"foundation"}` — reset local fixture state only.
- `POST /api/signals` with a `Signal` — idempotently persist normalized input and return linked impact/plan IDs.
- `POST /api/location` — accept paired phone coordinates; reject stale samples and mark low-accuracy samples uncertain.
- `GET /api/plans/{id}` — retrieve the current proposal version.
- `PATCH /api/plans/{id}` — revise allowed fields using `expected_version`.
- `POST /api/plans/{id}/approve` — approve and simulate execution atomically; duplicate-safe.
- `POST /api/plans/{id}/reject` — reject the exact current version.
- `GET /api/actions` — retrieve persisted action results/outbox history.

Person C's pure `engine.process(signal, events, contacts)` returns normalized
`Impact` and `Plan` records. Person D's `LifecycleService` owns exact-version
approval and execution. It currently writes only to the simulated outbox;
live providers remain disabled for Level A. A calendar
`ProviderResult` with `request_required=true` means the event must stay unchanged
and the executor should offer a proposal message instead.

`contracts/fixtures/foundation.json` covers location lateness;
`contracts/fixtures/flight.json` covers the delayed-flight meeting scenario.

`MessagingProvider.send(chat_id, text, action_id)` is the provider boundary.
`MESSAGING_MODE=mock` is the default; selecting unconfigured live mode returns a
truthful failure and never falls back to the simulated outbox.
