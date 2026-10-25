# Heads Up

**Heads Up is a proactive voice scheduling assistant.** It notices when an incoming request conflicts with your calendar, suggests a free alternative, lets you revise the plan by voice, and acts only after explicit approval.

This README is the complete demo walkthrough. The primary demo uses deterministic fixture data, a real browser voice session through Vapi, and safe mock calendar/messaging providers. Google Calendar and Beeper Desktop can replace the mock providers without changing the approval flow.

## The demo story

Your calendar already contains:

- **4:00–5:00 PM:** Team meeting
- **6:00–7:00 PM:** Evening appointment

While you are talking to Heads Up, Sam sends:

> Dinner today at 4:00 PM for 60 minutes?

Heads Up should:

1. Announce the new message during the active voice call.
2. Explain that 4 PM conflicts with the team meeting.
3. Suggest the first free slot, **5 PM**.
4. When told that 5 PM does not work, deterministically select **7 PM** because the 6 PM appointment blocks the intervening times.
5. Ask for approval of the revised plan.
6. After approval, create a tentative calendar event and then send the reply to Sam.

The calendar action always runs before messaging. If calendar creation fails, the reply is skipped rather than falsely claiming the time was reserved.

## Repository layout

```text
backend/    FastAPI, SQLite, scheduling engine, Vapi tools, providers and tests
frontend/   React + TypeScript demo interface
contracts/  Shared contract documentation and deterministic fixtures
ios/        Optional iPhone, AirPods and Apple Watch clients
```

## Quick start

Requirements: Python 3.11+, Node.js 20+, and a Vapi public key and saved assistant ID for voice.

```bash
git clone https://github.com/vibhusings/Heads_Up.git
cd Heads_Up
git checkout person-a-foundation
git pull origin person-a-foundation
```

### 1. Install the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Configure the frontend

Create `frontend/.env.local`:

```env
VITE_API_MODE=real
VITE_API_BASE=
VITE_VAPI_PUBLIC_KEY=your_vapi_public_key
VITE_VAPI_ASSISTANT_ID=your_saved_assistant_id
```

Never commit real keys or tokens.

### 3. Run the backend

From `backend/` with the virtual environment active:

```bash
HEADSUP_OFFLINE=1 \
CALENDAR_MODE=mock \
MESSAGING_MODE=mock \
VAPI_WEBHOOK_SECRET="your-vapi-webhook-secret" \
python -m headsup
```

The API runs at `http://localhost:8000`. The backend does **not** automatically load `backend/.env.local`; export variables or include them in the start command.

### 4. Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

If old Dinner Delay or Flight Delay buttons appear, you are running an older branch or cached frontend. Confirm `git log -1`, restart Vite, and hard-refresh the browser.

## Configure Vapi

Vapi needs a public HTTPS URL for the backend webhook. For example:

```bash
cloudflared tunnel --url http://localhost:8000
```

Use the resulting URL as:

```text
https://YOUR-TUNNEL-URL/api/voice/webhook
```

Print the exact current system prompt and tool schemas:

```bash
cd backend
source .venv/bin/activate
python -m headsup.voice
```

In the Vapi dashboard:

1. Open the saved Heads Up assistant.
2. Replace its system prompt with the printed `system_prompt`.
3. Create or update all printed tools: `get_plan`, `suggest_alternative`, `revise_plan`, `reject_plan`, and `approve_plan`.
4. Give every tool the public `/api/voice/webhook` URL.
5. Attach a Bearer credential whose value exactly matches `VAPI_WEBHOOK_SECRET`.
6. Set the first message to `I’m checking your current plans.`
7. Save the assistant.

Do not put the webhook secret in frontend code or tool JSON. Keep it in Vapi’s server-side credential configuration.

## Presentation script

### Reset and explain the calendar

Click **Reset demo**.

Say:

> “This is my current calendar. I have a team meeting at 4 PM and another appointment at 6 PM.”

### Start voice

Click **Start voice** and allow microphone access. Heads Up should say:

> “I’m checking your current plans.”

### Receive the request

Click **Receive message from Sam**.

Heads Up should announce that Sam requested dinner at 4 PM, explain the team-meeting conflict, and suggest 5 PM.

### Ask for another time

Say:

> “Five doesn’t work for me. Can you suggest something else?”

The assistant calls `suggest_alternative`. The backend—not the voice model—chooses the next precomputed conflict-free time: **7 PM**. The visible proposal should move to version 2.

### Approve

Say:

> “That works. Approve it and send the message.”

Expected result:

- A **Tentative: Dinner with Sam** event appears at 7 PM.
- Calendar creation appears first under **Completed actions**.
- The reply appears second as a simulated outbox action.
- Repeating approval does not execute either action again.

Suggested closing narration:

> “Heads Up connected an incoming message with my calendar, explained the conflict, found safe alternatives, accepted a spoken revision, and required approval before changing anything or messaging another person.”

## What is real and what is simulated?

| Part | Demo mode | Live-capable path |
| --- | --- | --- |
| Voice conversation | Real Vapi browser call | Same |
| Incoming message | Deterministic injected Beeper-shaped message | Beeper Desktop API polling |
| Conflict detection | Real deterministic backend calculation | Same |
| Plan revisions | Real versioned SQLite state | Same |
| Calendar creation | Mock calendar, labeled simulated | Google Calendar OAuth |
| Reply | Persisted simulated outbox | Beeper Desktop send |

Mock mode never claims an external action succeeded. Live mode never silently falls back to mock.

## Live Beeper Desktop messaging

Beeper Desktop must be open on the same machine as the backend.

1. In Beeper Desktop, open **Settings → Developers**, enable the API, create an approved connection, and copy its token.
2. Configure and inspect the local API:

```bash
export BEEPER_ACCESS_TOKEN="your-token"
export BEEPER_URL="http://localhost:23373"

cd backend
source .venv/bin/activate
python -m headsup.messaging check
python -m headsup.messaging chats
```

3. Start the backend with `MESSAGING_MODE=live`.
4. Map Sam to a verified one-to-one chat:

```bash
curl -X PUT http://localhost:8000/api/contacts/contact_sam/beeper-chat \
  -H "Content-Type: application/json" \
  -d '{"chat_id":"YOUR_CHAT_ID"}'
```

5. Poll the mapped chat:

```bash
curl -X POST http://localhost:8000/api/messaging/poll \
  -H "Content-Type: application/json" \
  -d '{"messages":null}'
```

Alternatively, set `BEEPER_POLL_INTERVAL` to a positive number of seconds. Only mapped one-to-one chats are read, and persisted message IDs prevent duplicate processing. Definite failures remain failed; uncertain sends are marked unknown and never retried blindly.

## Live Google Calendar

Use a dedicated test calendar and consenting participants. Authorize read access:

```bash
cd backend
source .venv/bin/activate
python -m headsup.calendar_oauth \
  --client-secrets /secure/client_secret.json \
  --token-file /secure/google-calendar-token.json
```

Start in live read mode:

```bash
CALENDAR_MODE=live \
GOOGLE_CALENDAR_TOKEN_FILE=/secure/google-calendar-token.json \
GOOGLE_CALENDAR_ID=primary \
python -m headsup
```

For writes, authorize again with `--write` and set `GOOGLE_CALENDAR_WRITE_ENABLED=true`. Calendar writes verify permissions and versions; live failures never become simulated successes.

## How it works

```text
Incoming message
      ↓
Strict scheduling parser
      ↓
Calendar conflict + free-slot calculation
      ↓
Versioned editable proposal
      ↓
Voice or button approval
      ↓
Tentative calendar action
      ↓ only if successful
Message action
      ↓
Persisted results and UI refresh
```

Important guarantees:

- The assistant never chooses arbitrary recipients.
- Ambiguous requests do not create executable plans.
- Every revision creates a new version; approval applies only to the reviewed version.
- Calendar creation and messaging use dependency ordering.
- Duplicate approval sends once.
- Interrupted writes become `unknown` instead of being blindly repeated.
- Provider credentials remain server-side.

## API overview

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Provider modes and readiness without secrets |
| `GET /api/session` | Calendar, messages, plans, approvals and results |
| `POST /api/demo/reset` | Reset local fixture state only |
| `POST /api/messaging/poll` | Poll mapped Beeper chats or inject mock messages |
| `GET /api/plans/{id}` | Read an exact plan/version |
| `PATCH /api/plans/{id}` | Revise message or complete start/end pair |
| `POST /api/plans/{id}/approve` | Approve and execute one version |
| `POST /api/plans/{id}/reject` | Reject without external action |
| `POST /api/voice/webhook` | Authenticated Vapi tool dispatcher |
| `GET /api/actions` | Persisted action outcomes |

Canonical contracts are documented in [`contracts/README.md`](contracts/README.md).

## Testing

Backend:

```bash
cd backend
source .venv/bin/activate
python -m pytest -q
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

Tests cover contracts, reset, persistence, voice revisions, stale versions, alternative selection, Beeper mapping and deduplication, calendar safety, dependency skipping, restart recovery, uncertain writes, and duplicate approval.

## Troubleshooting

### Voice only requests microphone permission

- Confirm the Vapi values are in `frontend/.env.local` and restart Vite.
- Check the browser console and the newest call in Vapi’s **Calls** page.
- Confirm the saved assistant has a model, voice, transcriber, prompt and all five tools.

### Voice works, but tools fail

- Confirm the backend has a public HTTPS tunnel.
- Confirm every tool URL ends in `/api/voice/webhook`.
- Confirm the Bearer credential matches `VAPI_WEBHOOK_SECRET`.
- Look for `POST /api/voice/webhook` in the backend terminal.

### The assistant mentions an old person or proposal

- Update the saved prompt and tools using `python -m headsup.voice`.
- Use the generic first message.
- Remove old Alex/Sam-specific text from the Vapi dashboard.

### The UI shows old scenarios

```bash
git checkout person-a-foundation
git pull origin person-a-foundation
```

Restart Vite and hard-refresh the browser.

### Live Beeper does not send

- Keep Beeper Desktop open.
- Run `python -m headsup.messaging check`.
- Verify Sam is mapped to a one-to-one chat.
- Inspect `/api/health` and `/api/actions` for provider errors.

## Current limitations

- Scheduling language uses a narrow deterministic grammar; ambiguous requests need clarification.
- The browser must remain open for the Vapi call.
- Live Beeper requires Desktop on the backend machine.
- Provider credentials are not included.
- Wearable, flight, phone-location and telephony prototypes remain in the repository but are outside this focused demo.

## Safety and privacy

Use test calendars, test chats and consenting teammates. Never commit `.env.local`, OAuth tokens, Beeper tokens, Vapi secrets, calendar client secrets, phone numbers, or personal message history.

Heads Up is built around reviewable proposals: detecting a conflict is not permission to act. Calendar and messaging actions occur only after explicit approval of the current plan version.
