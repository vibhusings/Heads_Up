import { ActionHistory } from "./components/ActionHistory";
import { DemoControls } from "./components/DemoControls";
import { ImpactCard } from "./components/ImpactCard";
import { ProposalCard } from "./components/ProposalCard";
import { Timeline } from "./components/Timeline";
import { useSession } from "./useSession";

export default function App() {
  const { session, health, error, refresh } = useSession();

  const plan = session?.plans.length
    ? [...session.plans].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0]
    : undefined;
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
            <ActionHistory results={session.results} actions={session.actions} contacts={session.contacts} />
          </div>
        </main>
      )}
    </>
  );
}
