import { useEffect, useMemo, useState } from "react";
import { ActionHistory } from "./components/ActionHistory";
import { DemoControls } from "./components/DemoControls";
import { ImpactCard } from "./components/ImpactCard";
import { PairingCard } from "./components/PairingCard";
import { ProposalCard } from "./components/ProposalCard";
import { Timeline } from "./components/Timeline";
import { VoicePanel } from "./components/VoicePanel";
import { useSession } from "./useSession";

export default function App() {
  const { session, health, error, refresh } = useSession();
  const orderedPlans = useMemo(() => session?.plans.length
    ? [...session.plans].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)) : [], [session?.plans]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  useEffect(() => {
    if (!orderedPlans.some((item) => item.id === selectedPlanId)) setSelectedPlanId(orderedPlans[0]?.id ?? null);
  }, [orderedPlans, selectedPlanId]);
  const plan = orderedPlans.find((item) => item.id === selectedPlanId) ?? orderedPlans[0];
  const impact = session?.impacts.find((item) => item.id === plan?.impact_id) ?? session?.impacts[0];
  const event = session?.events.find((item) => item.id === impact?.event_id);

  return (
    <>
      <DemoControls health={health} scenarioId={session?.scenario_id} onChanged={refresh} />
      {error && <div className="notice danger page">Backend unreachable: {error}</div>}
      {!session ? (
        <p className="muted page">Loading session…</p>
      ) : (
        <main className="app-shell">
          <section className="workspace-head">
            <div>
              <span className="eyebrow">Current workspace</span>
              <h1>{orderedPlans.length ? `${orderedPlans.length} plan${orderedPlans.length === 1 ? "" : "s"} to review` : "Everything looks clear"}</h1>
              <p className="muted">Choose a plan, review the suggestion, then approve or reject it.</p>
            </div>
          </section>

          {orderedPlans.length > 0 && (
            <nav className="plan-tabs" aria-label="Plans to review">
              {orderedPlans.map((item) => {
                const person = session.contacts.find((contact) => item.actions.some((action) => action.contact_id === contact.id));
                return (
                  <button key={item.id} className={item.id === plan?.id ? "plan-tab active" : "plan-tab"}
                    onClick={() => setSelectedPlanId(item.id)}>
                    <span>{person?.display_name ?? "Schedule"}</span>
                    <small>{item.status.replace(/_/g, " ")}</small>
                  </button>
                );
              })}
            </nav>
          )}

          {!impact || !plan ? (
            <section className="empty-state">
              <div className="empty-icon">✓</div>
              <h2>No conflicts detected</h2>
              <p>Use one of the demo buttons above to see Heads Up analyze a scheduling problem.</p>
            </section>
          ) : (
            <div className="review-grid">
              <ImpactCard impact={impact} event={event} signals={session.signals} />
              <ProposalCard key={plan.id} plan={plan} event={event} contacts={session.contacts}
                approvals={session.approvals} onChanged={refresh} />
            </div>
          )}

          <details className="details-card">
            <summary>Calendar <span>{session.events.length} events</span></summary>
            <Timeline events={session.events} impacts={session.impacts} />
          </details>

          <ActionHistory results={session.results} actions={session.actions} contacts={session.contacts} />

          <details className="details-card tools">
            <summary>Optional tools <span>voice and phone location</span></summary>
            <div className="tools-grid">
              <VoicePanel plan={plan?.status === "proposed" ? plan : undefined} />
              <PairingCard signals={session.signals} />
            </div>
          </details>
        </main>
      )}
    </>
  );
}
