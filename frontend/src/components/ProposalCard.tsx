import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { label, timeIn } from "../format";
import type { Approval, Contact, Event, Plan } from "../types";

type Notice = { tone: "info" | "warn" | "danger"; text: string } | null;
type Busy = null | "save" | "approve" | "reject";

interface Props {
  plan: Plan;
  event: Event | undefined;
  contacts: Contact[];
  approvals: Approval[];
  onChanged: () => void;
}

export function ProposalCard({ plan, event, contacts, approvals, onChanged }: Props) {
  const message = plan.actions.find((action) => action.type === "send_message");
  const serverText = message?.message_text ?? "";
  const [draft, setDraft] = useState(serverText);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const lock = useRef(false); // state updates are async; a double-click must not send twice
  const [notice, setNotice] = useState<Notice>(null);
  const tz = event?.timezone ?? "UTC";
  const editable = plan.status === "proposed";
  const approval = approvals.find((item) => item.plan_id === plan.id && item.plan_version === plan.version);

  // Follow the server's text whenever a new version arrives, unless the user is mid-edit.
  useEffect(() => {
    if (!dirty) setDraft(serverText);
  }, [plan.id, plan.version, serverText, dirty]);

  async function run(kind: Exclude<Busy, null>, call: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(kind);
    setNotice(null);
    try {
      setNotice({ tone: "info", text: await call() });
    } catch (exc) {
      if (exc instanceof ApiError && exc.isStale) {
        // Do not retry: show the user the version they now need to review.
        const latest = await api.getPlan(plan.id).catch(() => null);
        setDirty(false);
        setNotice({ tone: "warn", text: `Plan changed, review version ${latest?.version ?? "?"}` });
      } else {
        setNotice({ tone: "danger", text: exc instanceof Error ? exc.message : String(exc) });
      }
    } finally {
      lock.current = false;
      setBusy(null);
      onChanged();
    }
  }

  const save = () =>
    run("save", async () => {
      const revised = await api.revisePlan(plan.id, { expected_version: plan.version, message_text: draft });
      setDirty(false);
      return `Saved as version ${revised.version}. Any earlier approval no longer applies.`;
    });

  const approve = () =>
    run("approve", async () => {
      const outcome = await api.approvePlan(plan.id, plan.version);
      return outcome.duplicate
        ? `Version ${outcome.plan.version} was already executed; showing the same execution.`
        : `Approved version ${outcome.plan.version}: ${label(outcome.plan.status)}.`;
    });

  const reject = () =>
    run("reject", async () => {
      const rejected = await api.rejectPlan(plan.id, plan.version);
      return `Rejected version ${rejected.version}.`;
    });

  return (
    <section className="card">
      <div className="card-head">
        <h2>Proposal</h2>
        <span className="pill version">v{plan.version}</span>
        <span className={`pill status-${plan.status}`}>{label(plan.status)}</span>
      </div>

      {plan.actions.map((action) => {
        const contact = contacts.find((item) => item.id === action.contact_id);
        return (
          <div key={action.id} className="action-summary">
            <div>
              <span className="muted">Action</span> {label(action.type)}
              {" → "}
              {contact ? contact.display_name : <span className="danger-text">no recipient</span>}
            </div>
            {action.new_start_at && action.new_end_at && (
              <div>
                <span className="muted">Proposed time</span> {timeIn(action.new_start_at, tz)} –{" "}
                {timeIn(action.new_end_at, tz)}
              </div>
            )}
          </div>
        );
      })}

      {message && (
        <label className="draft">
          <span className="muted">Draft message</span>
          <textarea
            value={draft}
            disabled={!editable || busy !== null}
            rows={3}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(e.target.value !== serverText);
            }}
          />
        </label>
      )}

      <div className="approval-line">
        {approval ? (
          <span className="ok-text">
            Approved v{approval.plan_version} via {approval.approval_channel} at{" "}
            {timeIn(approval.approved_at, tz)}
          </span>
        ) : (
          <span className="muted">No approval for v{plan.version}</span>
        )}
        <span className="muted small"> · expires {timeIn(plan.expires_at, tz)}</span>
      </div>

      {notice && <div className={`notice ${notice.tone}`}>{notice.text}</div>}

      {editable && (
        <div className="buttons">
          <button onClick={save} disabled={!dirty || !draft.trim() || busy !== null}>
            {busy === "save" ? "Saving…" : "Save edit"}
          </button>
          <button className="primary" onClick={approve} disabled={dirty || busy !== null}>
            {busy === "approve" ? "Approving…" : `Approve v${plan.version}`}
          </button>
          <button className="danger" onClick={reject} disabled={busy !== null}>
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </button>
          {dirty && <span className="muted small">Save your edit before approving.</span>}
        </div>
      )}
    </section>
  );
}
