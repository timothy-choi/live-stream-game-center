import { GameCard } from "../components/GameCard";
import { useGameFeed } from "../hooks/useGameFeed";
import type { GameSummary } from "../api/types";

function Section({
  title,
  games,
}: {
  title: string;
  games: GameSummary[];
}) {
  if (games.length === 0) {
    return null;
  }
  return (
    <section className="feed-section" aria-labelledby={`${title}-heading`}>
      <h2 id={`${title}-heading`}>{title}</h2>
      <div className="card-grid">
        {games.map((game) => (
          <GameCard key={game.id} game={game} />
        ))}
      </div>
    </section>
  );
}

export function HomePage() {
  const { games, loading, error, status } = useGameFeed();
  const live = games.filter((game) => game.platform_lifecycle === "LIVE");
  const upcoming = games.filter((game) => game.platform_lifecycle === "UPCOMING");
  const ended = games.filter((game) => game.platform_lifecycle === "ENDED");

  return (
    <main className="page">
      <div className="page-head">
        <h1>Scoreboard</h1>
        {status === "reconnecting" || status === "resyncing" ? (
          <p className="banner banner-subtle">Updating live scores… last known scores stay visible.</p>
        ) : null}
      </div>
      {loading ? <p className="muted">Loading games…</p> : null}
      {error ? <p className="banner banner-warn">{error}</p> : null}
      {!loading && !error && games.length === 0 ? (
        <p className="empty">
          No synchronized games yet. The backend keeps watching configured channels even when this
          page is empty.
        </p>
      ) : null}
      <Section title="Live now" games={live} />
      <Section title="Upcoming" games={upcoming} />
      <Section title="Recently ended" games={ended} />
    </main>
  );
}
