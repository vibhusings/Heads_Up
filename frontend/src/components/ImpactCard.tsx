import { label, minutes, timeIn } from "../format";
import type { Event, Impact, Signal } from "../types";

function Term({ name, value }: { name: string; value: string | null | undefined }) {
  return (
    <div className="term">
      <span className="term-name">{name}</span>
      <span className="term-value">{value ?? "unknown"}</span>
    </div>
  );
}

export function ImpactCard({ impact, event, signals }: { impact: Impact; event: Event | undefined; signals: Signal[] }) {
  const tz = event?.timezone ?? "UTC";
  const used = signals.filter((signal) => impact.signal_ids.includes(signal.id));

  return (
    <section className="card">
      <h2>What changed</h2>
      <div className="calc">
        {used.map((signal) => (
          <div key={signal.id} className="calc-row">
            {signal.estimated_arrival_at && (
              <Term name={signal.kind.startsWith("flight") ? "Landing" : "Estimated arrival"} value={timeIn(signal.estimated_arrival_at, tz)} />
            )}
            <Term
              name="Trip"
              value={signal.route_duration_seconds == null ? null : minutes(signal.route_duration_seconds)}
            />
          </div>
        ))}
        <div className="calc-row result">
          <Term name="Earliest arrival" value={timeIn(impact.earliest_arrival_at, tz)} />
          <span className="vs">vs</span>
          <Term name="Event starts" value={event ? timeIn(event.start_at, tz) : null} />
          <span className="vs">=</span>
          <Term name="Late by" value={minutes(impact.late_by_seconds)} />
        </div>
        <p className="muted small">
          Arrival terms come from the backend. Buffers it applies (e.g. airport exit) are included in earliest
          arrival but not itemised yet.
        </p>
      </div>
      <dl className="facts">
        <dt>Reason</dt>
        <dd>{impact.reason}</dd>
        <dt>Confidence</dt>
        <dd>{impact.confidence || "unknown"}</dd>
        <dt>Mode</dt>
        <dd className={event?.mode === "unknown" || !event ? "warn-text" : ""}>{event ? label(event.mode) : "unknown"}</dd>
        <dt>Evidence</dt>
        <dd>
          {Object.keys(impact.evidence_times).length === 0 && "none recorded"}
          {Object.entries(impact.evidence_times).map(([key, at]) => (
            <div key={key}>
              {label(key)}: {timeIn(at, tz)}
            </div>
          ))}
        </dd>
      </dl>
    </section>
  );
}
