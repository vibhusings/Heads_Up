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
