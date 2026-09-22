import type { GameEvent, GameSummary, ScoreTimelineResponse } from "../api/types";

const stamp = "2026-09-21T20:00:00Z";

export const liveGame: GameSummary = {
  id: "11111111-1111-1111-1111-111111111111",
  platform: "youtube",
  external_broadcast_id: "hBblAZD-ApY",
  channel_id: "UCz3q0f-dMPNIyQhntPTE7gQ",
  title: "49ers Arcade",
  platform_lifecycle: "LIVE",
  tracking_status: "TRACKING",
  tracking_enabled: true,
  scheduled_start_at: stamp,
  actual_start_at: stamp,
  actual_end_at: null,
  matchup: {
    status: "CONFIRMED",
    left_team: { id: "SF", display_name: "San Francisco 49ers", abbreviation: "SF" },
    right_team: { id: "GB", display_name: "Green Bay Packers", abbreviation: "GB" },
    revision: 1,
    conflict_pair: null,
    conflict_count: 0,
  },
  score: { status: "TRUSTED", left: 21, right: 14, revision: 3 },
  completion_status: "UNVERIFIED",
  final_score_verified: false,
  postgame_left: null,
  postgame_right: null,
  final_score_conflict: null,
  recovery_status: "IDLE",
  score_history_completeness: "UNKNOWN",
  last_updated_at: stamp,
  cursor: "3:1:0:LIVE:TRACKING:1:UNVERIFIED:IDLE:UNKNOWN",
};

export const upcomingGame: GameSummary = {
  id: "22222222-2222-2222-2222-222222222222",
  platform: "youtube",
  external_broadcast_id: "upcomingVideo1",
  channel_id: "UCz3q0f-dMPNIyQhntPTE7gQ",
  title: "Later Arcade broadcast",
  platform_lifecycle: "UPCOMING",
  tracking_status: "TRACKING",
  tracking_enabled: true,
  scheduled_start_at: "2026-09-21T23:00:00Z",
  actual_start_at: null,
  actual_end_at: null,
  matchup: {
    status: "UNCONFIRMED",
    left_team: null,
    right_team: null,
    revision: 0,
    conflict_pair: null,
    conflict_count: 0,
  },
  score: { status: "UNCONFIRMED", left: null, right: null, revision: 0 },
  completion_status: "UNVERIFIED",
  final_score_verified: false,
  postgame_left: null,
  postgame_right: null,
  final_score_conflict: null,
  recovery_status: "IDLE",
  score_history_completeness: "UNKNOWN",
  last_updated_at: stamp,
  cursor: "0:0:0:UPCOMING:TRACKING:1:UNVERIFIED:IDLE:UNKNOWN",
};

export const endedGame: GameSummary = {
  id: "33333333-3333-3333-3333-333333333333",
  platform: "youtube",
  external_broadcast_id: "endedVideo0001",
  channel_id: "UCz3q0f-dMPNIyQhntPTE7gQ",
  title: "Completed Arcade broadcast",
  platform_lifecycle: "ENDED",
  tracking_status: "ACQUISITION_ENDED",
  tracking_enabled: true,
  scheduled_start_at: "2026-09-21T16:00:00Z",
  actual_start_at: "2026-09-21T16:05:00Z",
  actual_end_at: "2026-09-21T17:10:00Z",
  matchup: {
    status: "CONFIRMED",
    left_team: { id: "SF", display_name: "San Francisco 49ers", abbreviation: "SF" },
    right_team: { id: "NYG", display_name: "New York Giants", abbreviation: "NYG" },
    revision: 1,
    conflict_pair: null,
    conflict_count: 0,
  },
  score: { status: "TRUSTED", left: 28, right: 17, revision: 4 },
  completion_status: "UNVERIFIED",
  final_score_verified: false,
  postgame_left: null,
  postgame_right: null,
  final_score_conflict: null,
  recovery_status: "IDLE",
  score_history_completeness: "UNKNOWN",
  last_updated_at: stamp,
  cursor: "4:1:0:ENDED:ACQUISITION_ENDED:1:UNVERIFIED:IDLE:UNKNOWN",
};

export const conflictGame: GameSummary = {
  ...liveGame,
  id: "44444444-4444-4444-4444-444444444444",
  matchup: {
    ...liveGame.matchup,
    status: "CONFLICT",
    conflict_count: 1,
    conflict_pair: { left_team_id: "LAC", right_team_id: "LV" },
  },
};

export const timeline: ScoreTimelineResponse = {
  game_id: liveGame.id,
  total: 3,
  limit: 100,
  offset: 0,
  events: [
    {
      revision: 1,
      previous_left: null,
      previous_right: null,
      left: 0,
      right: 0,
      initialization: true,
      confirmed_at: stamp,
    },
    {
      revision: 2,
      previous_left: 0,
      previous_right: 0,
      left: 7,
      right: 0,
      initialization: false,
      confirmed_at: stamp,
    },
    {
      revision: 3,
      previous_left: 7,
      previous_right: 0,
      left: 21,
      right: 14,
      initialization: false,
      confirmed_at: stamp,
    },
  ],
};

export function gameEvent(
  type: GameEvent["type"],
  game: GameSummary,
  sequence = 1,
): GameEvent {
  return {
    event_id: `${type}-${sequence}`,
    sequence,
    game_id: game.id,
    type,
    occurred_at: stamp,
    score_revision: game.score.revision,
    matchup_revision: game.matchup.revision,
    cursor: game.cursor,
    payload: { game },
  };
}
