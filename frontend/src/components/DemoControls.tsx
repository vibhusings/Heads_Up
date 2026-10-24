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
      await api.reset(demo.scenario_id);
      await api.injectSignal(demo.signal);
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
        <button disabled={busy} onClick={() => inject("dinner")}>Inject dinner</button>
        <button disabled={busy} onClick={() => inject("flight")}>Inject flight</button>
        <button disabled={busy} onClick={() => act(() => api.reset(scenarioId ?? "foundation"))}>Reset</button>
      </div>
      {error && <div className="notice danger">{error}</div>}
    </header>
  );
}
