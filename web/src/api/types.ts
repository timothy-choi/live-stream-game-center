export type ScoreStatus = "UNCONFIRMED" | "TRUSTED";
export type MatchupStatus = "UNCONFIRMED" | "CONFIRMED" | "CONFLICT";
export type PlatformLifecycle = "UNKNOWN" | "UPCOMING" | "LIVE" | "ENDED";
export type TrackingStatus = "TRACKING" | "ACQUISITION_ENDED";

export type GameEventType =
  | "GAME_SNAPSHOT"
  | "SCORE_CONFIRMED"
  | "MATCHUP_CONFIRMED"
  | "MATCHUP_CONFLICT"
  | "PLATFORM_LIFECYCLE_CHANGED"
  | "TRACKING_STATUS_CHANGED"
  | "RESYNC_REQUIRED";

export interface TeamSummary {
  id: string;
  display_name: string;
  abbreviation: string | null;
}

export interface ConflictPair {
  left_team_id: string;
  right_team_id: string;
}

export interface MatchupSummary {
  status: MatchupStatus;
  left_team: TeamSummary | null;
  right_team: TeamSummary | null;
  revision: number;
  conflict_pair: ConflictPair | null;
  conflict_count: number;
}

export interface ScoreSummary {
  status: ScoreStatus;
  left: number | null;
  right: number | null;
  revision: number;
}

export interface GameSummary {
  id: string;
  platform: string;
  external_broadcast_id: string;
  channel_id: string | null;
  title: string | null;
  platform_lifecycle: PlatformLifecycle | string;
  tracking_status: TrackingStatus | string;
  tracking_enabled: boolean;
  scheduled_start_at: string | null;
  actual_start_at: string | null;
  actual_end_at: string | null;
  matchup: MatchupSummary;
  score: ScoreSummary;
  last_updated_at: string;
  cursor: string;
}

export interface GameListResponse {
  games: GameSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ScoreTimelineEvent {
  revision: number;
  previous_left: number | null;
  previous_right: number | null;
  left: number;
  right: number;
  initialization: boolean;
  confirmed_at: string;
}

export interface ScoreTimelineResponse {
  game_id: string;
  events: ScoreTimelineEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface GameEvent {
  event_id: string;
  sequence: number;
  game_id: string;
  type: GameEventType;
  occurred_at: string;
  score_revision: number;
  matchup_revision: number;
  cursor: string;
  payload: {
    game?: GameSummary;
    reason?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type YoutubeConnectionStatus = "CONNECTED" | "NEEDS_REAUTHORIZATION" | "DISCONNECTED";
export type CalibrationStatus = "READY" | "NEEDS_CALIBRATION";

export interface YoutubeConnectionSummary {
  id: string;
  provider: string;
  youtube_channel_id: string;
  youtube_channel_title: string | null;
  status: YoutubeConnectionStatus | string;
  calibration_status: CalibrationStatus | string;
  connected_at: string;
  last_refreshed_at: string | null;
}

export interface YoutubeConnectionListResponse {
  connections: YoutubeConnectionSummary[];
}

export class NotFoundError extends ApiError {
  constructor(message = "game not found") {
    super(404, message);
    this.name = "NotFoundError";
  }
}
