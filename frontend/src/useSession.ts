import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Health, SessionState } from "./types";

const POLL_MS = 1500;

// All screen state comes from GET /api/session; nothing is kept in browser storage.
export function useSession() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      setSession(await api.session());
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    api.health().then(setHealth).catch(() => setHealth(null));
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { session, health, error, refresh };
}
