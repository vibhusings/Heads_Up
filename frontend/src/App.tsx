import { useMemo } from "react";
import { ActionHistory } from "./components/ActionHistory";
import { DemoControls } from "./components/DemoControls";
import { ImpactCard } from "./components/ImpactCard";
import { ProposalCard } from "./components/ProposalCard";
import { Timeline } from "./components/Timeline";
import { VoicePanel } from "./components/VoicePanel";
import { useSession } from "./useSession";

export default function App() {
  const { session, health, error, refresh } = useSession();
  const orderedPlans = useMemo(() => session?.plans.length
    ? [...session.plans].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)) : [], [session?.plans]);
  const plan = orderedPlans[0];
  const impact = session?.impacts.find((item) => item.id === plan?.impact_id) ?? session?.impacts[0];
  const event = session?.events.find((item) => item.id === impact?.event_id);
  const message = session?.incoming_messages.find((item) => plan?.evidence_snapshot.incoming_message_id === item.id);
  const person = session?.contacts.find((contact) => plan?.actions.some((action) => action.contact_id === contact.id));
  const proposed = plan?.actions.find((action) => action.new_start_at);
  const proposedTime = proposed?.new_start_at
    ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit",
        timeZone: proposed.timezone ?? event?.timezone }).format(new Date(proposed.new_start_at)) : null;
  const announcement = plan && impact && message
    ? `You just received a message from ${person?.display_name ?? "your friend"}: “${message.text}” ${impact.reason} I suggest ${proposedTime}. Would you like to use that time?`
    : undefined;

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
              <span className="eyebrow">Today</span>
              <h1>Your schedule</h1>
              <p className="muted">Start the voice call, then receive Sam’s message to begin.</p>
            </div>
          </section>

          <details className="details-card" open>
            <summary>Current calendar <span>{session.events.length} events</span></summary>
            <Timeline events={session.events} impacts={session.impacts} />
          </details>

          <VoicePanel plan={plan?.status === "proposed" ? plan : undefined} announcement={announcement} />

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

          <ActionHistory results={session.results} actions={session.actions} contacts={session.contacts} />
        </main>
      )}
    </>
  );
}
