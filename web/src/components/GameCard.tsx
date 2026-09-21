import { Link } from "react-router-dom";
import type { GameSummary } from "../api/types";
import { formatUtc, scoreLabel, teamLabel, trackingHint } from "../display";
import { StatusBadge } from "./StatusBadge";

interface Props {
  game: GameSummary;
}

export function GameCard({ game }: Props) {
  const left = teamLabel(game.matchup.left_team);
  const right = teamLabel(game.matchup.right_team);
  const leftScore = scoreLabel(game.score.left, game.score.status);
  const rightScore = scoreLabel(game.score.right, game.score.status);
  const tracking = trackingHint(game);
  const scheduled = formatUtc(game.scheduled_start_at);
  const endedNote =
    game.platform_lifecycle === "ENDED" && game.score.status === "TRUSTED"
      ? "Latest tracked score"
      : null;

  return (
    <Link className="game-card" to={`/games/${game.id}`}>
      <div className="card-top">
        <StatusBadge lifecycle={game.platform_lifecycle} />
        {game.matchup.status === "CONFLICT" ? (
          <span className="review-flag">Identity needs review</span>
        ) : null}
      </div>
      <div className="card-sides">
        <div className="card-side">
          <span className="team-abbr">{left}</span>
          <span className="team-score" aria-label={`${left} score`}>
            {leftScore}
          </span>
        </div>
        <div className="card-side">
          <span className="team-abbr">{right}</span>
          <span className="team-score" aria-label={`${right} score`}>
            {rightScore}
          </span>
        </div>
      </div>
      <p className="card-context">
        {game.matchup.left_team && game.matchup.right_team
          ? `${game.matchup.left_team.display_name} vs ${game.matchup.right_team.display_name}`
          : game.title || "Broadcast"}
      </p>
      {scheduled && game.platform_lifecycle === "UPCOMING" ? (
        <p className="card-meta">{scheduled}</p>
      ) : null}
      {endedNote ? <p className="card-meta">{endedNote}</p> : null}
      {tracking ? <p className="card-meta">{tracking}</p> : null}
    </Link>
  );
}
