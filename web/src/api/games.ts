import { apiGet } from "./client";
import type { GameListResponse, GameSummary, ScoreTimelineResponse } from "./types";

export function listGames(fetchImpl?: typeof fetch): Promise<GameListResponse> {
  return apiGet<GameListResponse>("/api/games?limit=50", {}, fetchImpl);
}

export function getGame(gameId: string, fetchImpl?: typeof fetch): Promise<GameSummary> {
  return apiGet<GameSummary>(`/api/games/${encodeURIComponent(gameId)}`, {}, fetchImpl);
}

export function getScoreEvents(
  gameId: string,
  fetchImpl?: typeof fetch,
): Promise<ScoreTimelineResponse> {
  return apiGet<ScoreTimelineResponse>(
    `/api/games/${encodeURIComponent(gameId)}/score-events`,
    {},
    fetchImpl,
  );
}

export function isGameSummary(value: unknown): value is GameSummary {
  if (!value || typeof value !== "object") {
    return false;
  }
  const game = value as GameSummary;
  return (
    typeof game.id === "string" &&
    typeof game.external_broadcast_id === "string" &&
    typeof game.platform_lifecycle === "string" &&
    typeof game.cursor === "string" &&
    !!game.score &&
    !!game.matchup
  );
}
