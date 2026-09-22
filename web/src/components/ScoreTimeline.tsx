import type { ScoreTimelineEvent } from "../api/types";
import { formatUtc } from "../display";

interface Props {
  events: ScoreTimelineEvent[];
  loading?: boolean;
  error?: string | null;
}

export function ScoreTimeline({ events, loading = false, error = null }: Props) {
  return (
    <section className="panel" aria-labelledby="timeline-heading">
      <h2 id="timeline-heading">Confirmed score changes</h2>
      {loading ? <p className="muted">Loading score history…</p> : null}
      {error ? <p className="banner banner-warn">{error}</p> : null}
      {!loading && !error && events.length === 0 ? (
        <p className="muted">No confirmed score yet. The backend has not trusted a scoreboard reading.</p>
      ) : null}
      {events.length > 0 ? (
        <ol className="timeline">
          {[...events]
            .sort((left, right) => {
              const leftTime = left.observed_timestamp_s;
              const rightTime = right.observed_timestamp_s;
              if (leftTime != null && rightTime != null && leftTime !== rightTime) {
                return leftTime - rightTime;
              }
              return left.revision - right.revision;
            })
            .map((event) => (
            <li key={event.revision}>
              <span className="timeline-kind">
                {event.source === "POSTGAME_FINAL"
                  ? "Postgame"
                  : event.source === "RECOVERED_GAMEPLAY_SCOREBOARD"
                    ? "Recovered"
                    : event.initialization
                      ? "Start"
                      : "Update"}
              </span>
              <span className="timeline-score">
                {event.left} – {event.right}
              </span>
              <span className="timeline-time">{formatUtc(event.confirmed_at) ?? ""}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
