# Heads Up

A voice co-pilot for your day. It watches live data, stays silent while things are fine, and taps your wrist or whispers in your AirPods only when a decision is needed. Underneath is a multi-agent runtime that notices when it is stuck, looping or overspending and repairs itself.

```
backend/   Python: agents, Supervisor, Doctor, device gateway, judges' screen
ios/       Swift: iPhone app (wake word, AirPods voice, nod/shake) + Watch app (haptics, double-tap)
```

## Backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m headsup          # http://localhost:8000 is the control room
.venv/bin/python -m pytest           # 45 tests, all offline
ANTHROPIC_API_KEY=... .venv/bin/python -m headsup.live_check   # one real call per model step
```

With no `ANTHROPIC_API_KEY` set, the backend runs in **offline mode**: every model call is replaced by a deterministic heuristic, so the whole demo works without spending anything. Set the key to use live models.

### Master-plan API and Google Calendar

The stable team API is under `/api`: health, session state, fixture reset,
idempotent signal ingestion, versioned proposal editing, approval/rejection, and
simulated action history. Its canonical contracts and fixture are documented in
`contracts/README.md`.

With the backend running, exercise either complete Level A scenario with:

```bash
cd backend
.venv/bin/python -m headsup.demo --scenario foundation
.venv/bin/python -m headsup.demo --scenario flight
```

Calendar defaults to `CALENDAR_MODE=mock`. For a dedicated test calendar, enable
the Google Calendar API, create a Desktop OAuth client, then authorize read-only
access on the demo laptop (keep both files outside the repository):

```bash
cd backend
.venv/bin/python -m headsup.calendar_oauth \
  --client-secrets /secure/client_secret.json \
  --token-file /secure/google-calendar-token.json
export CALENDAR_MODE=live
export GOOGLE_CALENDAR_TOKEN_FILE=/secure/google-calendar-token.json
export GOOGLE_CALENDAR_ID=primary
```

Only after the read flow is stable, repeat authorization with `--write` and set
`GOOGLE_CALENDAR_WRITE_ENABLED=true`. Writes recheck the provider version,
reject recurring events, and return `request_required=true` for meetings owned
by another organizer.

| Variable | Default | What it does |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | unset | Turns on live models |
| `HEADSUP_MODEL_MAIN` | `claude-opus-5-5` | Planner, Researcher, Doctor |
| `HEADSUP_MODEL_FAST` | `claude-haiku-5-5` | Triage, Extractor, Judge, Concierge |
| `HEADSUP_DAILY_BUDGET` | `2.00` | Daily spending cap in USD |
| `HEADSUP_ANSWER_TIMEOUT` | `20` | Seconds to wait for a nod before moving up the channel ladder |
| `AVIATIONSTACK_KEY` | unset | Real flight data instead of the simulators |
| `HEADSUP_FLIGHT_POLL_REAL` | `600` | Seconds between real flight-data requests per flight |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | unset | Real SMS, and real calls together with `HEADSUP_PUBLIC_URL` |
| `HEADSUP_PUBLIC_URL` | unset | Public https URL of this server that Twilio can reach (e.g. an ngrok tunnel) |
| `DEEPGRAM_API_KEY` | unset | Live transcription of calls; without it calls connect but aren't transcribed |
| `HEADSUP_RECEIPT_AT` | `21:30` | When the end-of-day receipt plays |
| `HEADSUP_PAIRING_TOKEN` | unset | Required `X-Pairing-Token` value for `/api/location` |
| `HEADSUP_CORS_ORIGINS` | local Vite origins | Comma-separated frontend origins allowed to call `/api` |
| `MESSAGING_MODE` | `mock` | `mock` records a simulated outbox result; unconfigured `live` fails truthfully |
| `BEEPER_ACCESS_TOKEN` | unset | Beeper Desktop API token; with `MESSAGING_MODE=live`, approved messages go through Beeper |
| `BEEPER_URL` | `http://localhost:23373` | Beeper Desktop API address on the demo laptop |

### Real messages through Beeper

1. In Beeper Desktop: Settings → Developers → enable the API, then add an approved connection and copy its token.
2. `export BEEPER_ACCESS_TOKEN=...` and check it: `.venv/bin/python -m headsup.messaging check`
3. Find the teammate's one-to-one chat: `.venv/bin/python -m headsup.messaging chats`
4. Start the backend with `MESSAGING_MODE=live` and map the contact (verified live; group chats are refused):
   `curl -X PUT localhost:8000/api/contacts/contact_sam/beeper-chat -H 'Content-Type: application/json' -d '{"chat_id":"<id>"}'`
5. Trigger the scenario and approve. Check the message arrived: `.venv/bin/python -m headsup.messaging messages <id>`

Mappings survive a demo reset. A contact without a verified chat can't be messaged. If Beeper isn't running, the
send is `failed`; if Beeper doesn't answer clearly, it's `unknown` and is never resent.

Calls to Opus use server-side refusal fallbacks (`fallbacks: "default"`), so a declined request is re-run on another model automatically.

### Wearables: plans on the AirPods and Apple Watch

`headsup/wearables.py` connects the plan and approval flow to the iPhone app. When the engine creates a plan (or a plan is edited), the watch taps twice and the AirPods whisper the proposal. The answer goes through the same approve and reject as the buttons, for that exact plan version:

- **Nod or "yes"** approves; **shake or "no"** rejects; in a meeting, the proposal goes to the watch screen (double-tap approves, long-press rejects).
- **No answer, no action:** the plan stays proposed for the interface or voice.
- **Edited before you answer:** the new version is asked instead, and an answer to the old one is refused.
- **Result:** one soft tap when sent (or simulated), a long buzz when it failed.
- **Voice:** "Hey Heads Up, what's pending?", "approve it", "reject it".
- **No verified contact:** the draft is read out but never offered for approval.

**Watch direct mode:** the watch app can also connect straight to the backend at `/ws/watch/{user}`, getting the same haptics and questions the phone would relay. Answers are the only thing it can send. It's on by default in the watch simulator (no iPhone needed, backend at `ws://localhost:8000`) and can be switched on from the watch app's settings (gear icon) on a real watch.

It uses the phone's existing message format (`ios/Shared/Protocol.swift`), with `task_id` set to `plan:<plan_id>:v<version>`, so the app needs no changes for this. `HEADSUP_WEARABLE_USER` (default `vibhu`) names the phone that is asked. Without the phone, the control room's virtual device (`/sim/answer`) stands in.

### Running the demo from the control room

1. Click **AirPods in**, set a flight delay of 90 and click **Set**.
2. Vibhu's virtual device shows two taps and the spoken question. Click **Nod**.
3. Watch the agents negotiate (propose 8:15, Sam's agent counters 8:00, accept), the booking get verified, and both devices get one soft tap.
4. Turn on chaos faults one at a time and repeat. The tasks panel shows each repair and the playbook fills up with fixes it learned.
5. **Play the dinner call** runs the conversation flow: the Extractor finds the plan, the Researcher finds Thai Chef is closed, and the Judge offers to book an alternative.
6. Set the clock to 18:18 and click **Run at 60x**. At 6:20pm the watch gets the rising "leave now" pulses, plus a reminder of anything you promised on the call. Moving a plan moves its leave time.
7. **End-of-day receipt** plays the day's summary in the ear (it also plays by itself at `HEADSUP_RECEIPT_AT`, or when you say "what did you do today?").

### Flights

- **Which flights:** today's flights on your calendar. Events titled like "Flight UA 1234 ORD to DCA", "United 777 to Denver" or "B6 401 BOS-DCA" become tracked flights, with the event's end as the landing time. Plans entered by hand in `world.py` are tracked too; the demo has one (UA1234).
- **Simulated feeds** (no key): polled every 2 seconds. The control room sets the delay, and chaos mode can break the primary feed to show the switch to the backup.
- **Real data** (`AVIATIONSTACK_KEY`): each flight is polled at most every 10 minutes, and only from 3 hours before landing until an hour after. That's about 24 requests per flight per day; check your plan's monthly limit. If the real feed fails, Heads Up never falls back to the simulators: the data is marked stale and nothing acts on it until the feed recovers.
- **Not tracked yet:** flights on other days, and flights landing after midnight.

### Calendar: meetings and conflicts

The iPhone reads the next 7 days from every calendar on the phone (iCloud, Google, Outlook) through EventKit, read-only, and sends titles, times and locations to the backend. It sends them on every connect and whenever the calendar changes. Without the phone, **Sync demo calendar** in the control room sends a meeting happening now and a Friday 6:30pm study group.

- **Meetings:** while an event is happening, the backend marks you "in a meeting". Decisions then arrive as two taps on the watch with silent approval instead of a whisper. The manual "In meeting" button still works until the calendar's state next changes.
- **Conflicts:** when a call produces a plan ("Friday at 7"), it's checked against that day's calendar with a 30-minute travel buffer and a 90-minute assumed length. A clash is spoken ("that plan at 7pm overlaps your 6:30pm Study group"), and Heads Up won't offer to book a clashing time.
- **Not built:** writing to the calendar (adding holds).

### Hold the line for me

Click **"Call United and get me rebooked"**, or say it after "Hey Heads Up". Heads Up dials the (simulated) airline and picks "changes to an existing trip" from the menu. It then waits through the hold recordings. When Maria picks up, Heads Up introduces itself as an automated assistant, Vibhu's watch gets two taps, and the ear hears "United Airlines agent is on the line. Nod to join." A nod conferences Vibhu in.

| Chaos fault | What the system does |
| --- | --- |
| `ivr_loop` (menu keeps repeating) | The Supervisor spots the same key pressed over and over; the Doctor freezes the call and switches to pressing 0 for an operator |
| `hold_forever` | After `HEADSUP_HOLD_LIMIT` seconds (default 20 min) it asks "Still on hold after N minutes. Keep waiting?"; a shake hangs up |
| User never answers when the agent picks up | Climbs whisper → call, then apologizes to the agent ("they can't join right now, we'll call back") and hangs up |

Only lines that the keyword rules can't place (menu, hold recording, a person) go to a model, so a long hold costs almost nothing. With Twilio configured, the same task runs over a real call. Keys, speech and the conference are sent by updating the live call. Put a teammate's number in `world.py` businesses for the demo; the defaults are placeholders, so a test can never ring a real airline. Still to verify on a real call: whether Twilio keeps the audio stream running when the call is updated. The driver restarts the stream if it drops, but that path hasn't been exercised.

### Real phone calls

1. Run the server behind a public https URL, e.g. `ngrok http 8000`, and set `HEADSUP_PUBLIC_URL` to it.
2. Set the three Twilio variables and `DEEPGRAM_API_KEY`. Put real phone numbers in the profiles in `world.py`.
3. Say "Hey Heads Up, call Sam". Twilio rings your phone; when you answer, you and Sam both hear the consent notice, then you're connected. Each side of the call is transcribed separately, so every line is labeled with who said it. Hanging up sends the transcript to the Extractor.

Twilio webhooks are rejected unless their `X-Twilio-Signature` checks out. Raw audio is never stored.

The iPhone app connects to `ws://<laptop-ip>:8000/ws/device/vibhu`. Without it, the control room acts as the device.

### How the code maps to the plan

| Layer | Code |
| --- | --- |
| Gathers context | `agents/watchers.py` (FlightWatcher), `agents/context.py` (Extractor), `calendar_sync.py`, `telephony.py` |
| Thinks | `agents/thinkers.py` (Triage, Planner, Judge), `agents/concierge.py`, `agents/timekeeper.py` (leave now, receipt) |
| Does work | `agents/workers.py` (TaskRunner, Liaison, Guard, Verifier), `agents/context.py` (Researcher), `holdline.py` (Hold the line) |
| Talks to you | `devices.py`, `channels.py` |
| Keeps it healthy | `supervisor.py`, `doctor.py`, `contracts.py`, `ledger.py`, `events.py` (SQLite log) |

Every task runs under a `TaskContract` (allowed tools, budget, deadline, milestones, max repairs). The Supervisor is plain code and checks every tick for: blocked or stalled tasks, missed deadlines, repeated actions, task and daily overspend, missing heartbeats, flight feed errors and stale data, and repeated model errors. The Doctor works up a repair ladder (retry, switch source, replan, freeze, degrade, restart, escalate, safe stop) and saves fixes that worked to the playbook table.

### What's simulated

- **Flight data:** two simulated feeds share one truth, and chaos can break the primary. AviationStack is wired in when the key is set.
- **Restaurants:** the phone line and online booking are simulated; the Verifier checks the simulated reservation book independently of what the Caller reported.
- **Phone calls:** fully wired but only tested against fake Twilio and Deepgram streams. `/sim/call` feeds a transcript without a real call.
- **Research:** in live mode it uses Claude's server-side web search, which has no access to the user's accounts. Offline it uses a small fixed table.

## iOS and watchOS

You need a Mac with Xcode 16+, an iPhone on iOS 17+, AirPods with head tracking (Pro, Max or 3rd gen) and an Apple Watch on watchOS 11+ (Series 6 or newer). On any watch, double-tap the screen with a finger to approve and long-press to decline; the finger-pinch double tap also works on Series 9 / Ultra 2 and newer.

```bash
brew install xcodegen
cd ios && xcodegen && open HeadsUp.xcodeproj
```

1. Set your team under Signing for both targets (a free Apple ID works for your own devices).
2. Train "Hey Heads Up" for free in the [Picovoice Console](https://console.picovoice.ai), download the iOS `.ppn`, add it to the HeadsUp target as `hey-heads-up_ios.ppn`, and put your access key in `ios/HeadsUp/Secrets.swift`.
3. Run on the iPhone, enter the backend URL (`ws://<laptop-ip>:8000`) and tap Connect.
4. Run the Watch target once on the watch, and leave it open during the demo. Its workout session keeps custom haptics working with the wrist down.

The Swift code has not been compiled yet; expect small fixes on first build. If XcodeGen's watch embedding gives you trouble, create an iOS App with a Watch App in Xcode and drag in `HeadsUp/`, `HeadsUpWatch/` and `Shared/`.

| File | Does |
| --- | --- |
| `HeadsUp/Coordinator.swift` | Wires everything: backend messages in, speech and haptics out, answers back |
| `HeadsUp/VoiceLoop.swift` | Porcupine wake word plus stem squeeze backup |
| `HeadsUp/SpeechIO.swift` | Whispered speech out, on-device transcription in |
| `HeadsUp/HeadGestures.swift` | Nod and shake from AirPods motion; tune `threshold` on your AirPods |
| `HeadsUp/AudioRouteMonitor.swift` | Reports whether AirPods are in |
| `HeadsUp/CalendarSync.swift` | Reads the next 7 days from the phone's calendars (read-only) and sends them to the backend |
| `HeadsUp/WatchRelay.swift`, `HeadsUpWatch/PhoneLink.swift` | Phone-to-watch link |
| `HeadsUpWatch/HapticPlayer.swift` | The four vibration patterns |
| `HeadsUpWatch/HeadsUpWatchApp.swift` | Approval view: double-tap the screen (any watch) or pinch (Series 9+) |
| `Shared/Protocol.swift` | Message types shared with the backend |
