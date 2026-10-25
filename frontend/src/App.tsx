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
        <main className="grid">
          <div className="col">
            <Timeline events={session.events} impacts={session.impacts} />
            {impact ? (
              <ImpactCard impact={impact} event={event} signals={session.signals} />
            ) : (
              <section className="card muted">No disruption detected. Inject a scenario to start.</section>
            )}
          </div>
          <div className="col">
            <VoicePanel plan={plan?.status === "proposed" ? plan : undefined} />
            {orderedPlans.length > 0 && (
              <section className="card">
                <h2>Plans</h2>
                <div className="buttons">
                  {orderedPlans.map((item) => (
                    <button key={item.id} className={item.id === plan?.id ? "primary" : ""}
                      onClick={() => setSelectedPlanId(item.id)}>
                      {session.contacts.find((contact) => item.actions.some((action) => action.contact_id === contact.id))?.display_name ?? "Plan"}
                      {` · v${item.version} · ${item.status}`}
                    </button>
                  ))}
                </div>
              </section>
            )}
            {plan && (
              <ProposalCard
                key={plan.id}
                plan={plan}
                event={event}
                contacts={session.contacts}
                approvals={session.approvals}
                onChanged={refresh}
              />
            )}
            <PairingCard signals={session.signals} />
            <ActionHistory results={session.results} actions={session.actions} contacts={session.contacts} />
          </div>
        </main>
      )}
    </>
  );
}
