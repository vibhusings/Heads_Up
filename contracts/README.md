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
- `POST /api/plans/{id}/approve` — durably approve the exact version and queue it, then run the executor and return the results; duplicate-safe.
- `POST /api/plans/{id}/reject` — reject the exact current version.
- `GET /api/actions` — retrieve persisted action results.
- `GET /api/outbox` — `OutboxEntry` list: every message handed to the messaging provider, labeled `mock` or `live`.

Person C's pure `engine.process(signal, events, contacts)` returns normalized
`Impact` and `Plan` records. Person D's `LifecycleService` owns exact-version
approval; approving only records the approval and marks the plan `approved`.
`executor.Executor` is the only layer that acts. It resumes approved plans on
startup, saves a unique action key before each provider call, and never retries an
action whose outcome is unknown.

Action types the executor runs:

- `send_message` — needs `contact_id` and `message_text`. The contact must be in the
  directory and, when `event_id` is set, in that event's `contact_ids`; otherwise the
  result is `failed` / `INVALID_RECIPIENT` and nothing is sent. With
  `MESSAGING_MODE=live` the contact also needs a `beeper_chat_id`.
- `update_event` — needs `event_id`, `new_start_at`, `new_end_at`. The expected
  event version comes from `plan.evidence_snapshot["event_versions"][event_id]`,
  falling back to the stored event. A calendar `ProviderResult` with
  `request_required=true` fails the action and leaves the event unchanged; actions
  listing it in `depends_on_action_ids` are `skipped`, so the plan should include an
  independent request message instead.
- `create_event` — needs `event_title`, start/end, timezone and tentative status.
  Scheduling plans place it before a dependent reply, so a failed calendar hold
  skips the message instead of claiming the time was reserved.

Mock results are recorded as `simulated`, never `succeeded`. Timeouts are `unknown`.

`contracts/fixtures/foundation.json` covers location lateness;
`contracts/fixtures/flight.json` covers the delayed-flight meeting scenario.
`contracts/fixtures/combined.json` contains Sam and Alex together for additive demos.

`messaging.MessagingProvider.send_message(action_key, contact_id, chat_id, text)` is
the provider boundary. `MESSAGING_MODE=mock` is the default; selecting unconfigured
live mode returns a truthful `AUTH_REQUIRED` failure and never falls back to the
simulated outbox.

`POST /api/messaging/poll` reads only verified mapped Beeper chats (or explicit
mock messages in mock mode). Provider message IDs, inbound messages, and parsed
scheduling requests are persisted in `SessionState`, so polling is duplicate-safe.
