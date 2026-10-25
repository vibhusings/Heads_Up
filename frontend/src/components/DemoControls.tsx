import { useEffect, useRef, useState } from "react";
import { api, API_MODE } from "../api";
import type { Health } from "../types";

export function DemoControls({ health, scenarioId, onChanged }: { health: Health | null; scenarioId?: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || !scenarioId || scenarioId === "conversation") return;
    initialized.current = true;
    api.reset("conversation").then(onChanged).catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
  }, [onChanged, scenarioId]);

  async function act(call: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await call();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  const injectRequest = () => act(async () => {
    if (scenarioId !== "conversation") await api.reset("conversation");
    await api.pollMessages([{
      id: "demo_sam_dinner_request",
      contact_id: "contact_sam",
      chat_id: "simulated:contact_sam",
      senderName: "Sam",
      text: "Dinner today at 4:00 PM for 60 minutes?",
      timestamp: "2026-10-24T15:00:00Z",
    }]);
  });

  return (
    <>
      <header className="topbar">
        <div className="brand"><span className="brand-mark">H</span><strong>Heads Up</strong></div>
        <div className="system-state">
          <span className={health?.status === "ok" ? "status-dot ok" : "status-dot"} />
          {health?.status === "ok" ? "System ready" : "Check setup"}
          <span className="mode-label">{API_MODE} · {scenarioId ?? "loading"}</span>
        </div>
        <details className="status-menu">
          <summary>System details</summary>
          <div className="status-popover">
            {health && Object.entries(health.providers).map(([name, info]) => (
              <div key={name}><span>{name.replace(/_/g, " ")}</span><strong>{info.mode ?? info.status ?? "unknown"}</strong></div>
            ))}
          </div>
        </details>
      </header>
      <section className="demo-bar">
        <div><strong>Voice scheduling demo</strong><span className="muted">Reset, start voice, then receive Sam’s message.</span></div>
        <div className="buttons">
          <button className="secondary" disabled={busy} onClick={() => act(() => api.reset("conversation"))}>Reset demo</button>
          <button className="primary" disabled={busy} onClick={injectRequest}>Receive message from Sam</button>
        </div>
        {error && <div className="notice danger">{error}</div>}
      </section>
    </>
  );
}
