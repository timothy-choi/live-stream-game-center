import type { GameSummary } from "../api/types";
import { scoreLabel, teamName, trackingHint } from "../display";
import { StatusBadge } from "./StatusBadge";

interface Props {
  game: GameSummary;
}

export function Scoreboard({ game }: Props) {
  const leftName = teamName(game.matchup.left_team);
  const rightName = teamName(game.matchup.right_team);
  const leftAbbr = game.matchup.left_team?.abbreviation || game.matchup.left_team?.id || "TBD";
  const rightAbbr = game.matchup.right_team?.abbreviation || game.matchup.right_team?.id || "TBD";
  const tracking = trackingHint(game);

  return (
    <header className="scoreboard" aria-live="polite">
      <div className="scoreboard-status">
        <StatusBadge lifecycle={game.platform_lifecycle} />
        {game.matchup.status === "CONFLICT" ? (
          <span className="review-flag">Identity needs review</span>
        ) : null}
        {tracking ? <span className="tracking-hint">{tracking}</span> : null}
        {game.platform_lifecycle === "ENDED" && game.score.status === "TRUSTED" ? (
          <span className="tracking-hint">Latest tracked score</span>
        ) : null}
      </div>
      <div className="scoreboard-grid">
        <div className="scoreboard-team">
          <p className="team-full">{leftName}</p>
          <p className="team-short">{leftAbbr}</p>
        </div>
        <p className="scoreboard-score">
          <span>{scoreLabel(game.score.left, game.score.status)}</span>
          <span className="score-rule" aria-hidden="true">
            –
          </span>
          <span>{scoreLabel(game.score.right, game.score.status)}</span>
        </p>
        <div className="scoreboard-team scoreboard-team-right">
          <p className="team-full">{rightName}</p>
          <p className="team-short">{rightAbbr}</p>
        </div>
      </div>
      {game.title ? <p className="scoreboard-title">{game.title}</p> : null}
    </header>
  );
}
