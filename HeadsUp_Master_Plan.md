# HeadsUp Project Master Plan

Version 1.0 · October 24, 2026 · Four person hackathon team

## 1 Project overview

HeadsUp is a proactive personal assistant that detects when a real-world disruption threatens a commitment, identifies the people affected, and helps the user resolve it through messaging, calendar changes, and voice.

The project fits the Invisible Interfaces theme: the user should not have to inspect several apps, calculate the consequences, compose explanations, and manually coordinate every change. HeadsUp does that preparation and presents a specific action for approval. A small interface remains useful for setup, evidence, corrections, and a visible action history.

**Product statement:** When your plans change, HeadsUp helps the right people know and helps you repair the affected commitments.

This document is the implementation and handoff reference. It defines product behavior, technical boundaries, provider choices, shared contracts, four parallel workstreams, ten delivery tasks, testing, and a demo that survives unfinished optional work. All implementation items below are planned work, not claims that the application already exists.

**Delivery rule:** Complete tasks 1–4 as a connected system first. Those four tasks produce a demonstrable product. Tasks 5–10 enhance it without becoming prerequisites. Any arbitrary four tasks will not guarantee a demo; the four foundation tasks are deliberately chosen to do so.

### Decisions to follow immediately

- Build a single-user application on one designated demo laptop, using React with TypeScript, FastAPI, and SQLite.
- Use fixtures and deterministic message templates before depending on live APIs or an LLM.
- Make text editing and button approval work before adding voice.
- Treat flight delay and location lateness as interchangeable inputs to the same impact engine.
- Use Beeper for connected messaging, Google Calendar for calendar access, FlightAware for flight status, Google Routes for travel time, and Vapi for browser voice.
- Start with a verified contact directory. A full social graph and autonomous telephone negotiation are future work.
- Clearly distinguish simulated inputs, simulated actions, submitted messages, and verified external outcomes.

### How to use this document

Everyone reads sections 1–5 and 8–10. Each owner then follows their workstream in section 6 and provider details in section 7. The integration owner maintains the acceptance checklist and demo release. Sections 11–15 cover behavior, reliability, testing, and presentation.

## 2 The problem and intended user experience

The target user has commitments distributed across a calendar and personal conversations. A flight delay or long journey changes what is feasible, but the affected people do not automatically know. The user must connect travel information with event timing, decide what can move, identify recipients, and send updates.

HeadsUp connects those facts. Its useful contribution is the relationship between a disruption, a commitment, and the appropriate response. A voice interface alone does not establish that value; the underlying consequence analysis must work.

### Flight disruption example

A user has registered a flight and an in-person meeting. The flight was expected at 2:30 PM but is now expected at 3:45 PM. Assume a configurable 30-minute airport exit buffer and a 35-minute journey. The earliest plausible arrival is 4:50 PM, so a 4 PM meeting is threatened.

HeadsUp explains the calculation, suggests a later time that fits the user's calendar, and drafts a message to the mapped attendee. The user can say, “Propose 5:30, and make the message less formal.” The assistant submits the proposal. It does not claim that another attendee has accepted it.

If the user organizes the meeting and explicitly approves a time change, HeadsUp may update the organizer event and notify guests. If another person organizes it, HeadsUp sends a request and keeps the original event unchanged. Event modification permission and attendee agreement are separate concerns.

### Location lateness example

Dinner is at 7 PM. At 6:40 PM, travel time is 60 minutes. Estimated arrival is 7:40 PM, which means 40 minutes late. HeadsUp drafts, “Hey Sam, I’m running about 40 minutes late. ETA around 7:40—sorry!” The user approves, and HeadsUp sends once to the verified chat.

Being far away is not itself a reason to message. The system compares arrival time with event start, uses the selected travel mode, and checks that the location sample is recent enough.

### Relationship context example

The user can label a contact as family, friend, or colleague and choose a preferred channel and tone. The same disruption may justify an informal dinner update and a formal meeting request. Relationship labels guide wording; they do not authorize disclosure of unrelated messages, itinerary details, or precise location.

## 3 Scope boundaries

| Capability | Foundation demo | Later hackathon enhancement | After the hackathon |
|---|---|---|---|
| Events | Seeded events and editable details | Read Google Calendar | Multiple providers and recurring-event controls |
| Disruptions | Manual scenario injection | Live flight and location adapters | Broader disruption sources |
| Reasoning | Deterministic time and conflict rules | More candidate times and travel-aware checks | Multi-event optimization |
| Messages | Editable draft and simulated outbox | Send through one connected Beeper network | Broad network coverage and reply negotiation |
| Voice | Text and buttons | Browser voice approval and edits | Calls to other people |
| Calendar changes | Simulated updates | Update eligible test events | Complex recurring and shared-event policies |
| Relationships | Manually verified contacts | Directory editing and preferences | Social graph discovery and visualization |
| Location | Seeded coordinates or ETA | Foreground phone location | Native background collection |

Do not build flight booking, ticket changes, restaurant reservation integrations, a mobile app, graph databases, or multi-user authentication during the initial sprint. A restaurant meeting notification goes to the person you are meeting, not to the restaurant unless that is separately implemented and authorized.

## 4 Demo levels and stop points

| Level | Required work | What can be demonstrated honestly |
|---|---|---|
| A Functional prototype | Tasks 1–4 | Scenario causes an impact; user edits and approves; simulated outbox records the exact action and survives refresh |
| B Connected assistant | A plus task 5 | Same workflow sends a real message to a consenting teammate through Beeper |
| C Voice assistant | B plus task 6 | User approves or revises the same plan by voice, with buttons still available |
| D Calendar recovery | C plus tasks 7–8 | Live event reading and authorized calendar changes with explicit results |
| E Live context | D plus either task 9 or 10 | Real flight status or foreground location drives the existing workflow |

Level A is a working software prototype, but it is not a live integration demonstration. Level B is the minimum target for a credible connected product. If time permits only one external integration, prioritize real messaging over a live flight feed. A labeled injected delay is predictable; a verified outgoing message proves useful action.

Maintain a known-good demo release after every level. Missing providers must disable only their own capability. Do not silently fall back from a failed live send to a simulated success.

## 5 Ten implementation tasks

Time estimates are planning ranges for focused work by the indicated owner, excluding unfamiliar account setup. They are not guarantees. Tasks 1–4 proceed in parallel against agreed fixtures and merge into the first release.

| Task | Owner | Dependencies | Estimate | Acceptance criterion |
|---|---|---|---|---|
| 1 Contracts and runnable foundation | A | None | 45–75 min | Backend health, shared JSON fixtures, SQLite initialization, reset command and documented run commands work |
| 2 Demo interface | B | Task 1 contracts; mocks allowed | 60–120 min | Timeline, impact card, editable proposal, approve/reject and action history work with fixture responses |
| 3 Impact and proposal engine | C | Task 1 contracts | 60–120 min | Both sample scenarios yield correct lateness, affected event, recipient IDs and a deterministic draft |
| 4 Approval and execution lifecycle | D | Task 1 contracts; integrates 2–3 | 60–120 min | Approved plan executes into a simulated outbox; duplicate approval does not duplicate an action; state survives refresh |
| 5 Real Beeper messaging | D | Task 4 | 45–90 min plus setup | Explicitly mapped test chat receives approved text once; provider errors appear truthfully |
| 6 Voice interaction | B | Tasks 2 and 4 | 60–120 min plus setup | Voice reads active proposal, edits it, and approves the correct version; button fallback remains |
| 7 Calendar reading | A | Tasks 1 and 3 | 45–90 min plus OAuth | Upcoming timed events normalize into the shared Event model; unavailable calendars are handled |
| 8 Calendar rescheduling | A with D review | Tasks 4 and 7 | 60–120 min | Eligible event updates; external-organizer event becomes a request; stale plans cannot overwrite changes |
| 9 Live flight monitoring | C | Task 3 | 45–90 min plus API access | Correct flight instance is selected and a new arrival estimate generates a normalized signal |
| 10 Location and route ETA | C with B support | Task 3; phone UI support | 60–120 min plus setup | Fresh phone coordinates plus destination produce an ETA; stale/denied location cannot trigger a misleading auto-action |

Task 4 includes the first integrated rehearsal. It is not done merely because an executor function exists. Tasks 9 and 10 are alternatives if C is overloaded: complete whichever provider is ready first. Other owners help only after the core demo passes.

## 6 Four person work split

Assign actual names at kickoff. The labels below define ownership, not seniority. Gitansh is a reasonable fit for A or C because those roles concentrate Python logic, but the team should use actual strengths and familiarity.

### Person A owns backend foundation and calendar

Own shared Pydantic models, application startup, database schema, health/status endpoints, fixture seed/reset, and calendar adapters. Serve as integration coordinator and maintain the known-good demo tag. Publish contracts before implementing provider specifics.

First deliverable: a backend that serves the agreed seeded session and accepts scenario inputs. Then integrate C's engine and D's executor through narrow functions. After Level A passes, implement calendar reads and then writes. A owns schema migrations; other people propose changes rather than independently editing schema files.

### Person B owns interface and voice

Own React screens, API client types, proposal editing, voice panel, phone location page, and demo presentation. Build against local fixtures immediately; do not wait for backend readiness. Match the agreed contracts rather than creating UI-only field names.

First deliverable: an interactive demo screen with visible evidence and simulated results. Then attach Vapi to the same approval and revision endpoints. Support C with the phone location page only after the browser demo is stable.

### Person C owns disruption analysis and travel adapters

Own pure impact-calculation functions, candidate-time logic, fixture scenarios, flight adapter, and Routes adapter. Keep arithmetic deterministic and independent of the frontend, Vapi, and provider response formats.

First deliverable: tested functions that consume normalized events and signals and return impact/proposal data. After Level A, add one live travel input. Do not let live monitoring become a prerequisite for generating a proposal.

### Person D owns messaging and execution reliability

Own contact-to-chat mapping, messaging adapter, outbox/action lifecycle, approval execution, duplicate protection, and provider outcome logging. Coordinate with A for database access through the shared repository layer.

First deliverable: a simulated messaging provider and persisted action results. Then replace only that provider with Beeper. Verify actual messages on a teammate's device. Review A's calendar mutation path so it uses the same approval and execution rules.

### Collaboration rules

- Use one repository and short feature branches. Merge small working changes frequently; do not wait until the end to integrate.
- Suggested file ownership: A owns `backend/app.py`, `models.py`, `db.py`, `providers/calendar.py`; B owns `frontend/`; C owns `engine/`, `providers/flights.py`, `providers/routes.py`; D owns `execution/`, `providers/messaging.py`.
- Keep fixtures under `contracts/fixtures/` and one contract change log. A approves breaking changes after notifying all owners.
- Each provider implements the same interface in mock and live modes. Everyone develops against mock implementations when credentials are unavailable.
- Merge checks: app starts, foundation scenario passes, no secret committed, no change to response shape without contract update.
- Shared integration checkpoint every 45–60 minutes. Each owner shows running behavior, names a blocker, and states the next mergeable increment.
- Only the designated demo machine needs live Beeper credentials. Never assume a teammate's `localhost` points to that machine.

## 7 External services and setup

Provider capabilities below are based on the official documentation listed in section 16. Pricing, account access, and platform behavior must be verified during kickoff. Budget for paid APIs; no free-tier entitlement is assumed.

### Beeper Desktop API

Purpose: read selected chats and submit messages through connected networks. It runs locally inside Beeper Desktop, which must remain open. The documented local server is `http://localhost:23373`; requests require authentication. Available message history may be incomplete. [1–3]

Setup owner D installs or opens Beeper on the demo machine, connects one supported network, enables developer API access, creates the supported token, lists chats, and manually verifies one teammate chat ID. Test one explicit message before adding automation. Keep the token only in backend configuration.

Application adapter: `list_chats()`, `read_recent_messages(chat_id, limit)`, and `send_message(chat_id, text)`. Consult the current API reference for exact SDK names and payloads. Prefer polling selected chats for optional reply handling; an experimental event stream is not necessary for this demo.

A provider acknowledgment means submitted or sent according to that provider's semantics, not necessarily delivered or read. Use a teammate's receiving device to verify the demo outcome. Do not claim exactly-once delivery across an ambiguous network timeout.

### Google Calendar API

Purpose: read upcoming events, query permitted free/busy information, and update eligible events. Use user OAuth, not an assumed service-account access path to a personal calendar. Configure a Google Cloud project, enable Calendar, set the consent configuration/test users as needed, create the correct OAuth client, and register the exact redirect URI. Keep refresh tokens private. [4–6]

Recommended initial permissions: `calendar.events.readonly` for reading. Request `calendar.events` when implementing event writes and `calendar.freebusy` only if needed for free/busy queries. Verify the selected methods' current accepted scopes. Use a dedicated test calendar and consenting teammates.

Use `events.list` with a bounded time window, normalize timezone-aware start/end values, and expand recurring instances when reading. Exclude all-day events from automatic travel analysis. For the MVP, do not mutate recurring series. Use `events.patch` for specific supported changes, recheck the event first, and send guest updates when appropriate.

The organizer owns shared event timing. Even when an event is technically editable, moving it does not mean attendees accepted the new time. Unknown availability must remain unknown. For another organizer's meeting, default to a proposal message and retain the original calendar entry.

### FlightAware AeroAPI

Purpose: obtain flight status and estimated arrival information. AeroAPI offers usage-based access and alert capabilities; availability depends on the account and service tier. [7]

Owner C obtains access, tests a known flight lookup, and records one concrete flight instance. Identify it by flight number, date, route, and provider instance ID when available. Flight number alone is insufficient because it repeats across days and can be ambiguous across codeshares.

Normalize scheduled and estimated arrival, destination airport, status, observation time, and source. Use gate arrival when supplied; if using landing time instead, document and adjust the airport buffer rather than treating the two as equivalent. Missing estimates are unknown, not zero delay. Cancellation is a separate disruption and should not fabricate an ETA.

Suggested hackathon polling interval: five minutes for one active flight, configurable and subject to quota. A manual refresh button is sufficient. Do not implement webhooks until polling works. Keep a recorded or seeded signal for the live presentation.

### Google Maps Routes API

Purpose: compute journey duration between coordinates. Enable Routes and required billing, restrict the API key, and call `computeRoutes` from the backend using the chosen travel mode. For driving, request traffic-aware routing and the required field mask, including duration. [8]

Seed the restaurant/meeting destination coordinates to avoid a Places or Geocoding dependency. If destinations later arrive only as free-form calendar text, add geocoding as a separate enhancement and ask the user to resolve ambiguous places.

Cache results briefly, refresh only when needed, and record retrieval time. Do not request a new route for every GPS update. A failed lookup yields unknown ETA or an explicitly labeled cached estimate.

### Browser location

Purpose: obtain coordinates from an open phone page using `navigator.geolocation.watchPosition()`. This requires user permission and a secure context. It does not guarantee precise GPS or background updates. [9]

Owner B provides a paired phone page. Owner C receives latitude, longitude, reported accuracy, and capture time. Use a short-lived pairing token so arbitrary visitors cannot post coordinates. A laptop's location is not the user's phone location unless that is intentionally the source.

For the MVP, treat samples older than two minutes as stale and low-accuracy samples above a configurable threshold as uncertain. These are product defaults, not platform guarantees. Allow typed or simulated input with an explicit label.

### Vapi browser voice

Purpose: voice conversation with the user. Use the Web SDK for the browser session and server tools for application actions. Configure the assistant, public client credential, server-side secret, and authenticated tool webhook. Vapi handles the voice stack; do not build separate speech recognition and speech synthesis integrations for the MVP. [10–11]

Tools should retrieve the current proposal, revise it, reject it, and approve a specific version. Execution remains in our backend. The voice model must not choose arbitrary chat IDs, construct provider URLs, or bypass approval state.

Start with a user-initiated voice session. A proactive visual alert can invite the user to talk; do not promise the browser will always start speaking or listening in the background. If voice fails, preserve the same plan and expose the button workflow.

### Slashy MCP and the LLM decision

Slashy's official integration covers email, calendar, meeting preparation, contact research, and scheduled workflows. MCP exposes tools to agents; it is not a universal source of flight or GPS data. [12]

Default decision: omit Slashy because direct Calendar plus Beeper already cover the selected workflow. If a teammate has working Slashy access, it can replace the calendar adapter only after proving the required reads/writes, authentication, and result semantics. Do not integrate both calendar paths during the sprint.

The foundation engine needs no LLM. Use fixed templates with editable text. Vapi supplies conversational reasoning for voice. An optional separate text model may later improve drafts, but provider/model selection should use an account already available to the team. It must accept structured context and return schema-validated text; it never determines time arithmetic or authorization.

## 8 Architecture and deployment

Run React, FastAPI, SQLite, and Beeper on the designated demo laptop. The backend orchestrates rules and provider calls. The frontend displays state and submits user intent. SQLite persists the prototype session, contacts, plans, and action history.

| Component | Receives | Produces | Boundary |
|---|---|---|---|
| Input adapters | Flight API, phone coordinates, seeded scenario | Normalized signals | No message sending |
| Calendar adapter | Provider or seeded events | Normalized events | No impact reasoning |
| Impact engine | Events, signals, route estimates | Impacts and candidate actions | Pure deterministic logic |
| Proposal service | Impact, contacts, candidate times | Versioned editable plan | No external writes |
| Approval service | Authenticated user action and plan version | Approval record | Rejects stale versions |
| Executor | Approved plan | Provider results and action state | Only layer permitted to write externally |
| Interface and voice | Backend state | User revisions and approval | Cannot bypass execution rules |

Expose a narrow secured HTTPS application endpoint through a tunnel for Vapi callbacks and the phone page if required. Keep Beeper's raw local API private. Authenticate the application, validate Vapi webhook credentials according to current documentation, and keep secrets server-side. The Vapi public client key is distinct from private server credentials.

Use simple frontend polling for prototype state updates. Avoid a distributed worker system or message queue. Run only one monitor/executor process for the demo to avoid duplicate scheduler jobs. Persist provider mode separately for calendar, messages, flights, and routes.

Suggested feature flags: `MESSAGING_MODE=mock|live`, `CALENDAR_MODE=mock|live`, `FLIGHTS_MODE=mock|live`, `ROUTES_MODE=mock|live`, `VOICE_ENABLED=true|false`, and `AUTO_NOTIFY_ENABLED=false`. A mock input may drive a live message only in an explicit demo session with the real recipient and approval clearly shown.

For a future cloud deployment, Beeper will need a local companion that connects outward to the cloud service. A hosted backend cannot reach the user's laptop by calling its own localhost. Do not build that bridge during the hackathon.

## 9 Shared data contracts

Define these with Pydantic in Python and matching TypeScript types. Store timestamps as ISO 8601 UTC values and keep IANA event/user timezones for display. Use seconds for durations and meters for accuracy. IDs are stable opaque strings. Provider payloads stay inside adapters.

| Entity | Required fields |
|---|---|
| Contact | id, display_name, relationship, email optional, beeper_chat_id optional, preferred_channel, tone, auto_notify_allowed |
| Event | id, provider, provider_event_id optional, title, start_at, end_at, timezone, mode, destination optional, contact_ids, organizer_self, writable, provider_version optional |
| Signal | id, kind, source, source_mode, observed_at, flight_instance_id optional, estimated_arrival_at optional, position optional, route_duration_seconds optional |
| Impact | id, event_id, signal_ids, earliest_arrival_at, late_by_seconds, reason, confidence, evidence_times, status |
| Plan | id, version, impact_id, status, actions, created_at, expires_at, evidence_snapshot |
| Action | id, type, contact_id optional, event_id optional, message_text optional, new_start_at optional, new_end_at optional, depends_on_action_ids, status |
| ActionResult | action_id, provider, provider_mode, status, provider_reference optional, error_code optional, completed_at optional |
| Approval | id, plan_id, plan_version, user_id, approved_at, approval_channel |

`mode` on Event is `in_person`, `remote`, or `unknown`. Uncertain event mode or destination requires clarification before travel-based action. `confidence` is a rule-based label with reasons, not an invented calibrated probability.

### Example normalized impact

```json
{
  "id": "impact_dinner_001",
  "event_id": "event_dinner_001",
  "signal_ids": ["signal_location_001"],
  "earliest_arrival_at": "2026-10-25T02:40:00Z",
  "late_by_seconds": 2400,
  "reason": "A 60 minute journey at 18:40 reaches the 19:00 dinner at 19:40",
  "confidence": "high",
  "evidence_times": {"route_observed_at": "2026-10-25T01:40:00Z"},
  "status": "proposed"
}
```

The example corresponds to October 24 in America/Los_Angeles. Store timezones explicitly rather than hard-coding this UTC offset. For a repeatable demo, a seeded clock controls both current time and fixture events.

## 10 Backend and provider interfaces

These are our proposed application endpoints, not vendor endpoints. Freeze their shapes in task 1.

| Endpoint | Input | Output and behavior |
|---|---|---|
| GET /api/health | None | Provider health and enabled modes; no secrets |
| GET /api/session | Session auth | Events, contacts, impacts, active plans and recent results |
| POST /api/demo/reset | scenario_id | Reset mock state only; never erase a real calendar or resend messages |
| POST /api/signals | Normalized signal | Signal ID and resulting impact/plan IDs; duplicate input safe |
| POST /api/location | Coordinates, accuracy, captured_at | Accepted/stale status; paired-device auth required |
| GET /api/plans/{id} | Plan ID | Current version, evidence, actions and status |
| PATCH /api/plans/{id} | expected_version, allowed edits | New version; previous approval invalidated |
| POST /api/plans/{id}/approve | expected_version | Durable approval and queued execution; stale version returns conflict |
| POST /api/plans/{id}/reject | expected_version | Rejected state; no external action |
| GET /api/actions | Session auth | Per-action results including simulated/unknown outcomes |
| POST /api/voice/webhook | Verified provider payload | Dispatch only allowlisted tools against the authenticated session |

Approval and execution must not depend on the frontend making a second successful request. The approval endpoint records approval and queues execution in the same local transaction; the single executor resumes pending work after restart. A separate internal execute function is not a public unauthenticated endpoint.

Provider interfaces should return domain objects, not raw HTTP responses:

```text
CalendarProvider.list_events(window) -> Event[]
CalendarProvider.get_availability(calendar_ids, window) -> AvailabilityResult
CalendarProvider.update_event(event_id, expected_version, changes) -> ProviderResult
MessagingProvider.send(chat_id, text, action_id) -> ProviderResult
FlightProvider.get_status(flight_instance) -> FlightStatus
RoutesProvider.estimate(origin, destination, mode, departure_at) -> RouteEstimate
```

The `action_id` passed to messaging is our correlation key. It is not proof that the vendor supports idempotency. Adapters must explicitly state whether they can safely retry a write.

Standard error codes: `AUTH_REQUIRED`, `PROVIDER_UNAVAILABLE`, `RATE_LIMITED`, `INVALID_RECIPIENT`, `STALE_PLAN`, `STALE_LOCATION`, `PERMISSION_DENIED`, and `OUTCOME_UNKNOWN`. Return a human-readable explanation and whether retry is safe.

## 11 Decision rules and scheduling behavior

### Time calculations

- Location: earliest arrival = current time + estimated journey duration + optional arrival buffer.
- Flight: earliest arrival = estimated airport arrival + airport exit buffer + onward travel duration + optional arrival buffer.
- Lateness = max(0, earliest arrival minus event start).
- Proposed new end = proposed new start + original event duration.

Suggested initial lateness threshold is ten minutes, configurable. Trigger after two consistent live observations when available; a manual demo signal can trigger immediately. Suggested airport exit buffer is 30 minutes for the example, not a universal travel fact. International arrival, luggage, terminal transfer, and uncertainty may require a different buffer.

Evaluate only relevant upcoming commitments, initially within 24 hours. Do not shift all later meetings. Remote events need separate feasibility rules and are not automatically disrupted by distance. Fixed events and unknown destinations should produce an alert rather than an automatic move.

### Candidate times

Start after the earliest plausible arrival, round up to a 15-minute boundary, preserve duration, and search the user's available working window. Exclude conflicts and include travel buffers where known. Offer at most three candidates. If attendee availability is inaccessible, label the options “free on your calendar; awaiting attendee agreement.” If no suitable slot exists, offer a message without a time proposal.

For the foundation demo, use one preconfigured candidate and validate it against seeded events. For later live calendar support, recheck availability and event version at execution time. If conditions changed, regenerate the plan and obtain new approval.

### Communication rules

Identify recipients from manually verified mappings. Unknown or ambiguous contacts block sending. Preserve the original meeting time unless the approved action explicitly changes it. Notify only affected people, using only the context required to explain the issue.

An approved change to an organizer event may notify attendees through Calendar. Do not also send a duplicate message unless the plan explicitly includes that separate communication. A message that says “I moved our meeting” depends on the calendar update succeeding. Prefer “Could we move our meeting?” when agreement is still pending.

Do not infer acceptance from silence, an emoji, or an ambiguous reply. Reply negotiation is deferred. Optional automatic family updates require a per-contact opt-in, a bounded message type, fresh evidence, and a cooldown. Keep this off in the initial demo.

## 12 Execution state and reliability

Plan states: `proposed`, `approved`, `executing`, `completed`, `partially_failed`, `failed`, `rejected`, and `expired`. Revisions increment the version and invalidate prior approvals. Approval applies to exact action content and recipient IDs, not a vague permission to do anything related to the event.

Action states: `pending`, `executing`, `succeeded`, `simulated`, `failed`, `unknown`, and `skipped`. A plan can complete with simulated actions only when clearly marked as a simulation. Never represent `unknown` as success.

Persist a unique action key before contacting a provider. Duplicate approval requests should return the existing execution. For a timeout after sending, reconcile using provider identifiers/history when possible; otherwise mark unknown and avoid blind retries. Preventing duplicates locally cannot guarantee exactly-once behavior across all external systems.

For multiple actions, respect declared dependencies. If a calendar write fails, skip a dependent message claiming the event moved. Independent messages can succeed while another action fails; display that partial result. Do not automatically undo successful messages or event updates because another action failed. A correction is a new explicit action.

Suggested defaults: five-minute proposal expiry, one lateness notification per event unless ETA changes materially, and a 15-minute notification cooldown. Recheck evidence before execution. These defaults are tunable product choices.

### Failure fallback matrix

| Failure | Fallback | Required visible behavior |
|---|---|---|
| Flight API unavailable | Inject recorded/seeded delay | Label flight input simulated |
| Routes unavailable | User-provided or seeded journey time | Label ETA source and uncertainty |
| Phone permission denied | Manual/simulated input | No claim of live tracking |
| Voice unavailable | Text editing and buttons | Preserve plan and action state |
| Calendar OAuth blocked | Seeded calendar | Do not claim a real event changed |
| Beeper disconnected | Keep draft or use explicit simulation | Live send remains failed; no fake success |
| Recipient ambiguous | User selects a mapped contact | No send until resolved |
| Network timeout after write | Reconcile or mark unknown | No blind duplicate retry |
| Entire network unavailable | Fully local Level A demo | Explain that all external actions are simulated |

## 13 Build schedule and integration gates

The actual hackathon duration is unspecified. Use this eight-hour example as a sequencing template and scale the optional blocks, not the final rehearsal period.

| Elapsed time | A | B | C | D | Gate |
|---|---|---|---|---|---|
| 0–30 min | Contracts and repo | Screen skeleton | Fixtures and formulas | Beeper access probe | Names assigned, demo machine chosen, shared shapes agreed |
| 30–120 min | Backend and persistence | Mock interface | Impact/proposal functions | Approval and mock executor | Tasks 1–4 integrate |
| 120–180 min | Fix integration | Fix flow and presentation | Edge-case checks | Duplicate and restart checks | Level A tagged and recorded |
| 180–270 min | Calendar read | Voice | Flight or route adapter | Real Beeper send | Level B first, then C |
| 270–360 min | Calendar write if stable | Voice polish or phone page | Complete one live input | Execution review and recovery | Optional capabilities behind flags |
| 360–420 min | Freeze and verify | Pitch and screen polish | Fixture reset and evidence | Recipient and provider checks | No new integrations |
| 420–480 min | Full rehearsal with team | Full rehearsal with team | Full rehearsal with team | Full rehearsal with team | Demo and fallback both rehearsed |

If tasks 1–4 are not integrated by the first major checkpoint, everyone pauses optional provider work. B and A connect UI to backend; C and D connect proposal to executor. If Beeper is blocked, keep the draft/copy workflow and label the demo accordingly rather than spending the entire event debugging one network.

## 14 Tests and definition of done

Use small tests for the important behavior, plus one end-to-end scenario. Provider mocks are test dependencies, not substitute evidence for live integration claims.

| Test | Expected result |
|---|---|
| Dinner at 19:00, now 18:40, journey 60 minutes | Arrival 19:40, lateness 40 minutes |
| Flight arrival 15:45, exit 30 minutes, journey 35 minutes | Arrival 16:50; 16:00 meeting threatened |
| Same flight disruption, feasible later event | Later event remains unaffected |
| Remote event with no travel constraint | No distance-only automatic reschedule |
| Duplicate signal or approval | One plan/execution outcome; no repeated message |
| Edit after approval | New plan version requires new approval |
| Stale event version or changed availability | Reject old execution and propose review |
| Missing contact mapping | Draft allowed; sending blocked |
| External organizer | Send request; do not claim shared event moved |
| Stale phone location | No confident automatic lateness action |
| Provider send timeout | Unknown outcome, no blind retry |
| Calendar failure before dependent message | Message claiming success is skipped |
| Refresh or backend restart | Persisted plan and action results remain visible |
| Cross-timezone or overnight event | Correct UTC arithmetic and local display |

Foundation acceptance: start from clean seeded state, trigger a scenario, inspect evidence, edit a draft, approve, see a simulated result, refresh, and confirm no duplication. All four owners must be able to explain the real/simulated boundary.

Live messaging acceptance: the intended teammate sees the approved text in the correct conversation. Calendar acceptance: inspect the provider event after the update. Voice acceptance: spoken edits modify the current plan, and approval applies to that version. A voice transcript alone is not evidence that the external action succeeded.

## 15 Demo script and presentation

### Two minute primary demo

1. Explain the problem in one sentence: “A delay changes your plans, but the people waiting for you do not know.”
2. Show today's meeting and the mapped attendee. State which inputs are simulated.
3. Inject a flight arrival change. Show the earliest-arrival calculation and the threatened meeting.
4. Show the proposed message and candidate time. Speak an edit and approve if voice is ready; otherwise use the visible controls.
5. Show the action result and the teammate's receiving device. If calendar writing is implemented, show the eligible updated event; otherwise describe the message as a rescheduling proposal.
6. Close with the location example only if it is already reliable. Explain that a different signal uses the same engine.

### Foundation fallback demo

Use the same story with seeded input, editable draft, button approval, and simulated outbox. Say: “The workflow is running locally; these provider actions are simulated.” Demonstrate persistence and duplicate protection. Do not present a prerecorded external action as something executed live.

### Rehearsal checklist

- Demo laptop charged, awake, and signed in; Beeper running if used.
- Test calendar, contact mapping, and recipient device ready.
- Microphone permission, network, tunnel, and webhook authentication verified if voice is used.
- Provider modes visible and no real private conversations exposed on the presentation screen.
- Seeded clock and event times consistent; reset affects only mock state.
- Record a backup run after the last stable release and label it as recorded if shown.
- Freeze the known-good commit; disable incomplete optional features rather than leaving broken controls.

### What to claim

Claim the capabilities actually demonstrated: consequence detection, contextual proposal, approval, and verified actions. Describe background mobile tracking, social graph discovery, multi-party negotiation, and autonomous phone calls as future work unless implemented and tested.

## 16 Sources and integration references

Official references consulted for the integration plan on October 24, 2026. These establish provider capabilities; the architecture, task order, thresholds, and time estimates are project design decisions.

1. [Beeper Desktop API overview](https://developers.beeper.com/desktop-api/) — local operation, connected networks, history limitations.
2. [Beeper API authentication](https://developers.beeper.com/desktop-api/auth) — token and authentication setup.
3. [Beeper API reference](https://developers.beeper.com/desktop-api-reference/) — current methods and payloads.
4. [Google Calendar API reference](https://developers.google.com/workspace/calendar/api/v3/reference) — events and free/busy operations.
5. [Google Calendar event patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch) — event mutation parameters.
6. [Google Calendar attendee and organizer semantics](https://developers.google.com/workspace/calendar/api/concepts/inviting-attendees-to-events) — shared event ownership and propagation.
7. [FlightAware AeroAPI](https://www.flightaware.com/commercial/aeroapi/) — flight status, estimates, alerts, access and pricing information.
8. [Google Routes computeRoutes](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes) — route duration and traffic-aware calculations.
9. [Browser geolocation watchPosition](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/watchPosition) — permission and secure-context requirements.
10. [Vapi web calls](https://docs.vapi.ai/quickstart/web) — Web SDK and browser voice integration.
11. [Vapi tools](https://docs.vapi.ai/tools) — application tool integration.
12. [Slashy official integration repository](https://github.com/Slashy-com/skills) — MCP capability scope.

## 17 Kickoff checklist

- Assign A, B, C, and D to named teammates and select the demo laptop.
- Agree on a cutoff time and a maximum API spend before enabling paid services.
- Pick one consenting message recipient, one demo calendar, one dinner destination, and one tracked flight instance.
- Freeze the contracts, commit fixtures, and implement tasks 1–4 in parallel.
- Pass and tag Level A before making live integrations critical.
- Add real messaging first, then voice, then calendar/live context as time permits.
- Keep the final rehearsal block protected. A smaller complete workflow is the release target.
