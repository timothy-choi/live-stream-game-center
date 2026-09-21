import { Link, useParams } from "react-router-dom";
import { Scoreboard } from "../components/Scoreboard";
import { ScoreTimeline } from "../components/ScoreTimeline";
import { YouTubeEmbed } from "../components/YouTubeEmbed";
import { formatUtc } from "../display";
import { useGameDetail } from "../hooks/useGameDetail";

export function GameDetailPage() {
  const { gameId } = useParams();
  const { game, timeline, loading, notFound, error, timelineError, status } = useGameDetail(gameId);

  if (loading && !game) {
    return (
      <main className="page">
        <p className="muted">Loading game…</p>
      </main>
    );
  }

  if (notFound) {
    return (
      <main className="page">
        <h1>Game not found</h1>
        <p className="muted">That game id is not in the durable catalog.</p>
        <Link to="/">Back to scoreboard</Link>
      </main>
    );
  }

  if (!game) {
    return (
      <main className="page">
        <h1>Unable to load game</h1>
        <p className="banner banner-warn">{error}</p>
        <Link to="/">Back to scoreboard</Link>
      </main>
    );
  }

  return (
    <main className="page">
      <p className="crumb">
        <Link to="/">Scoreboard</Link>
      </p>
      {status === "reconnecting" || status === "resyncing" ? (
        <p className="banner banner-subtle">Reconnecting… showing last known durable state.</p>
      ) : null}
      {error ? <p className="banner banner-warn">{error}</p> : null}
      <Scoreboard game={game} />
      <section className="panel meta-grid">
        <p>
          <span className="meta-label">Scheduled</span>
          {formatUtc(game.scheduled_start_at) ?? "—"}
        </p>
        <p>
          <span className="meta-label">Started</span>
          {formatUtc(game.actual_start_at) ?? "—"}
        </p>
        <p>
          <span className="meta-label">Ended</span>
          {formatUtc(game.actual_end_at) ?? "—"}
        </p>
        <p>
          <span className="meta-label">Broadcast</span>
          {game.external_broadcast_id}
        </p>
      </section>
      <ScoreTimeline events={timeline} error={timelineError} />
      <YouTubeEmbed externalBroadcastId={game.external_broadcast_id} />
    </main>
  );
}
