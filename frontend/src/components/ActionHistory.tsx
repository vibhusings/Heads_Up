import { label } from "../format";
import type { Action, ActionResult, Contact } from "../types";

const REASONS: Record<string, string> = {
  DEPENDENCY_FAILED: "Skipped: an action it depends on did not succeed",
  INVALID_RECIPIENT: "No valid recipient or message",
};

export function ActionHistory({
  results,
  actions,
  contacts,
}: {
  results: ActionResult[];
  actions: Action[];
  contacts: Contact[];
}) {
  const byId = new Map(actions.map((action) => [action.id, action]));

  return (
    <section className="card">
      <h2>Completed actions</h2>
      {results.length === 0 && <p className="muted">Approved actions will appear here.</p>}
      <ul className="history">
        {[...results].reverse().map((result) => {
          const action = byId.get(result.action_id);
          const contact = contacts.find((item) => item.id === action?.contact_id);
          return (
            <li key={result.action_id} className={`result result-${result.status}`}>
              <div className="result-head">
                <span className={`pill status-${result.status}`}>{label(result.status)}</span>
                <span className={result.provider_mode === "live" ? "pill live" : "pill simulated"}>
                  {result.provider_mode === "live" ? "LIVE" : "SIMULATED"}
                </span>
                <span className="muted small">
                  {label(action?.type ?? "action")} → {contact?.display_name ?? "—"} via {result.provider}
                </span>
              </div>
              {action?.message_text && <blockquote>{action.message_text}</blockquote>}
              {result.error_code && (
                <div className="danger-text small">{REASONS[result.error_code] ?? result.error_code}</div>
              )}
              {result.status === "unknown" && (
                <div className="warn-text small">Outcome unknown — check the provider before retrying.</div>
              )}
              <div className="muted small">
                {result.action_id}
                {result.provider_reference && ` · ${result.provider_reference}`}
                {result.completed_at && ` · ${new Date(result.completed_at).toLocaleTimeString()}`}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
