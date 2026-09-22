import type { GameEvent, GameSummary, MatchupSummary, ScoreSummary } from "./types";

function newerScore(current: ScoreSummary, incoming: ScoreSummary): ScoreSummary {
  return incoming.revision >= current.revision ? incoming : current;
}

function newerMatchup(current: MatchupSummary, incoming: MatchupSummary): MatchupSummary {
  if (incoming.revision > current.revision) {
    return incoming;
  }
  if (incoming.revision < current.revision) {
    if (incoming.conflict_count > current.conflict_count) {
      return {
        ...current,
        status: incoming.status === "CONFLICT" ? "CONFLICT" : current.status,
        conflict_count: incoming.conflict_count,
        conflict_pair: incoming.conflict_pair ?? current.conflict_pair,
      };
    }
    return current;
  }
  if (incoming.conflict_count > current.conflict_count) {
    return {
      ...incoming,
      left_team: current.left_team ?? incoming.left_team,
      right_team: current.right_team ?? incoming.right_team,
    };
  }
  return incoming;
}

function completionRank(status: string | undefined): number {
  if (status === "FINAL") {
    return 2;
  }
  if (status === "FINALIZING") {
    return 1;
  }
  return 0;
}

export function mergeGame(current: GameSummary | null, incoming: GameSummary): GameSummary {
  if (!current) {
    return incoming;
  }
  const completion =
    completionRank(incoming.completion_status) >= completionRank(current.completion_status)
      ? incoming.completion_status
      : current.completion_status;
  return {
    ...incoming,
    completion_status: completion,
    score: newerScore(current.score, incoming.score),
    matchup: newerMatchup(current.matchup, incoming.matchup),
  };
}

export function eventGame(event: GameEvent): GameSummary | null {
  const game = event.payload?.game;
  if (!game || typeof game !== "object") {
    return null;
  }
  return game;
}
