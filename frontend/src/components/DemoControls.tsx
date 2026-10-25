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
    <header className="topbar">
      <div className="brand">
        <strong>Heads Up</strong> <span className="muted">control room</span>
      </div>
      <div className="badges">
        <span className={API_MODE === "mock" ? "pill warn" : "pill"}>API: {API_MODE}</span>
        {scenarioId && <span className="pill">scenario: {scenarioId}</span>}
        {health &&
          Object.entries(health.providers).map(([name, info]) => (
            <span key={name} className={info.mode === "live" && info.status !== "disabled" ? "pill live" : "pill"}>
              {name}: {info.mode ?? "?"}
              {info.status && info.status !== "ok" ? ` (${info.status})` : ""}
            </span>
          ))}
      </div>
      <div className="buttons">
        <button disabled={busy} onClick={() => act(() => api.reset("combined"))}>Load all mock data</button>
        <button disabled={busy} onClick={() => inject("dinner")}>Inject dinner</button>
        <button disabled={busy} onClick={() => inject("flight")}>Inject flight</button>
        <button disabled={busy} onClick={injectRequest}>Inject Sam request</button>
        <button disabled={busy} onClick={() => act(() => api.reset(scenarioId ?? "combined"))}>Reset</button>
      </div>
      {error && <div className="notice danger">{error}</div>}
    </header>
  );
}
