import { useState } from "react";
import { api, API_MODE, DEMOS } from "../api";
import type { DemoScenario, Health } from "../types";

export function DemoControls({ health, scenarioId, onChanged }: { health: Health | null; scenarioId?: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const inject = (name: DemoScenario) =>
    act(async () => {
      const demo = DEMOS[name];
      if (scenarioId !== "combined") await api.reset("combined");
      await api.injectSignal(demo.signal);
    });

  const injectRequest = () => act(async () => {
    if (scenarioId !== "combined") await api.reset("combined");
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
        <div><strong>Try a scenario</strong><span className="muted">Load data, then add one or more disruptions.</span></div>
        <div className="buttons">
          <button className="secondary" disabled={busy} onClick={() => act(() => api.reset("combined"))}>1. Load demo</button>
          <button disabled={busy} onClick={() => inject("dinner")}>Dinner delay</button>
          <button disabled={busy} onClick={() => inject("flight")}>Flight delay</button>
          <button disabled={busy} onClick={injectRequest}>New message from Sam</button>
          <button className="quiet" disabled={busy} onClick={() => act(() => api.reset(scenarioId ?? "combined"))}>Clear</button>
        </div>
        {error && <div className="notice danger">{error}</div>}
      </section>
    </>
  );
}
