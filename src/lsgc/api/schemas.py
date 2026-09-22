"""Product-facing JSON contracts. Independent of persistence dataclasses."""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScoreStatus(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    TRUSTED = "TRUSTED"


class MatchupStatus(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    CONFLICT = "CONFLICT"


class GameEventType(str, Enum):
    GAME_SNAPSHOT = "GAME_SNAPSHOT"
    SCORE_CONFIRMED = "SCORE_CONFIRMED"
    MATCHUP_CONFIRMED = "MATCHUP_CONFIRMED"
    MATCHUP_CONFLICT = "MATCHUP_CONFLICT"
    PLATFORM_LIFECYCLE_CHANGED = "PLATFORM_LIFECYCLE_CHANGED"
    TRACKING_STATUS_CHANGED = "TRACKING_STATUS_CHANGED"
    RESYNC_REQUIRED = "RESYNC_REQUIRED"


class TeamSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    abbreviation: str | None = None


class ConflictPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_team_id: str
    right_team_id: str


class MatchupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MatchupStatus
    left_team: TeamSummary | None = None
    right_team: TeamSummary | None = None
    revision: int = 0
    conflict_pair: ConflictPair | None = None
    conflict_count: int = 0


class ScoreSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScoreStatus
    left: int | None = None
    right: int | None = None
    revision: int = 0


class GameSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    platform: str
    external_broadcast_id: str
    channel_id: str | None = None
    title: str | None = None
    platform_lifecycle: str
    tracking_status: str
    tracking_enabled: bool
    scheduled_start_at: str | None = None
    actual_start_at: str | None = None
    actual_end_at: str | None = None
    matchup: MatchupSummary
    score: ScoreSummary
    last_updated_at: str
    cursor: str


class GameListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    games: list[GameSummary]
    total: int
    limit: int
    offset: int


class ScoreTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int
    previous_left: int | None = None
    previous_right: int | None = None
    left: int
    right: int
    initialization: bool
    confirmed_at: str


class ScoreTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: UUID
    events: list[ScoreTimelineEvent]
    total: int
    limit: int
    offset: int


class GameEvent(BaseModel):
    """Realtime envelope. `sequence` is per-game and monotonic in-process."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int
    game_id: UUID
    type: GameEventType
    occurred_at: str
    score_revision: int
    matchup_revision: int
    cursor: str
    payload: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    process: str
    database: str


class WorkerDiagnostic(BaseModel):
    """Read-only M9 worker snapshot. No media URLs or credentials."""

    model_config = ConfigDict(extra="forbid")

    broadcast_id: str
    state: str
    frames_emitted: int
    retry_count: int
    last_error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    next_retry_at: str | None = None


class OrchestratorDiagnostic(BaseModel):
    """Read-only M9 reconciliation status. Does not start tracking."""

    model_config = ConfigDict(extra="forbid")

    orchestrator_enabled: bool
    last_sync_at: str | None = None
    last_reconcile_at: str | None = None
    last_sync_ok: bool | None = None
    last_sync_error: str | None = None
    discovered_count: int = 0
    live_count: int = 0
    upcoming_count: int = 0
    ended_count: int = 0
    active_worker_count: int = 0
    workers: list[WorkerDiagnostic] = Field(default_factory=list)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


class YoutubeConnectionSummary(BaseModel):
    """Safe connection fields. Tokens and secrets are forbidden extras."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider: str
    youtube_channel_id: str
    youtube_channel_title: str | None = None
    status: str
    calibration_status: str
    connected_at: str
    last_refreshed_at: str | None = None


class YoutubeConnectionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: list[YoutubeConnectionSummary]
