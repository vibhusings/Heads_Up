# Heads Up frontend (Person B)

React + TypeScript control room for Level A. All state comes from `GET /api/session`
(polled every 1.5 s); nothing is kept in browser storage, and no arrival math,
versioning or status logic lives here.

```sh
# backend (from backend/)
HEADSUP_OFFLINE=1 CALENDAR_MODE=mock .venv/bin/python -m headsup   # :8000

# frontend (from frontend/)
npm install
npm run dev                          # real backend via the Vite /api proxy
VITE_API_MODE=mock npm run dev       # no backend: replays src/mocks/captured.json
```

`src/api.ts` is the only module that talks to the backend. Mock mode replays
responses recorded from the real backend; regenerate them after contract changes:

```sh
cd backend && .venv/bin/python ../frontend/scripts/capture_fixtures.py
```

Demo signals for the inject buttons live in `src/mocks/demoSignals.json`.
`backend/web/index.html` stays as the fallback until this app reaches parity.

## Voice (Vapi)

The browser only holds the Vapi **public** key and a saved assistant ID. The
assistant's tools call `POST /api/voice/webhook`, which checks a Bearer credential
against `VAPI_WEBHOOK_SECRET` (Vapi does not attach credentials to inline
assistants, so the assistant must be saved in the Vapi dashboard).

1. Expose the backend publicly, e.g. `cloudflared tunnel --url http://localhost:8000`.
2. Print the tool definitions and prompt: `cd backend && .venv/bin/python -m headsup.voice`.
3. In Vapi: create a Bearer Token credential holding your `VAPI_WEBHOOK_SECRET`;
   create the four tools with server URL `https://<tunnel>/api/voice/webhook` and
   that credential; create an assistant with the printed system prompt and the tools.
4. Run the backend with `VAPI_WEBHOOK_SECRET=...` and the frontend with
   `VITE_VAPI_PUBLIC_KEY=...` and `VITE_VAPI_ASSISTANT_ID=...`.

Voice tools refuse recipient fields, run message edits through the existing
`Guard`, and approve only the `expected_version` the assistant read back.
