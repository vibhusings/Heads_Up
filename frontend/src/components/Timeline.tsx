import { dateTimeIn, label, timeIn } from "../format";
import type { Event, Impact } from "../types";

export function Timeline({ events, impacts }: { events: Event[]; impacts: Impact[] }) {
  const affected = new Set(impacts.map((impact) => impact.event_id));
  const ordered = [...events].sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));

  return (
    <section className="card">
      <h2>Your calendar</h2>
      {ordered.length === 0 && <p className="muted">No events in this session.</p>}
      <ol className="timeline">
        {ordered.map((event) => {
          const hit = affected.has(event.id);
          return (
            <li key={event.id} className={hit ? "event affected" : "event"}>
              <div className="event-time">
                {timeIn(event.start_at, event.timezone)} – {timeIn(event.end_at, event.timezone)}
              </div>
              <div className="event-body">
                <strong>{event.title}</strong>
                <span className="muted">
                  {event.destination ?? "No location"} · {label(event.mode)} · {event.timezone}
                </span>
                <span className="muted small">{dateTimeIn(event.start_at, event.timezone)}</span>
              </div>
              <span className={hit ? "pill danger" : impacts.length ? "pill ok" : "pill"}>
                {hit ? "Affected" : impacts.length ? "Unaffected" : "No disruption"}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
