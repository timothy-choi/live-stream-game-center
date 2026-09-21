"""Durable game / trusted-score snapshots.

Persisted trusted score = temporally confirmed visual score.
It is NOT official, corrected, final, or winner-certified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from lsgc.identity.models import ConfirmedMatchupChange, ConfirmedMatchupState
from lsgc.track.models import ConfirmedScoreChange, TrustedScoreState

PLATFORM_YOUTUBE = "youtube"


class TrackingStatus(str, Enum):
    """Acquisition/tracking lifecycle. Not a game-final."""

    TRACKING = "TRACKING"
    """Process may apply observations for this broadcast."""

    ACQUISITION_ENDED = "ACQUISITION_ENDED"
    """M4 acquisition ended. Trusted score is frozen. Not FINAL."""


class PlatformLifecycle(str, Enum):
    """YouTube/platform broadcast lifecycle. Not an acquisition state and not FINAL."""

    UNKNOWN = "UNKNOWN"
    UPCOMING = "UPCOMING"
    LIVE = "LIVE"
    ENDED = "ENDED"


class PersistOutcome(str, Enum):
    APPLIED = "applied"
    IDEMPOTENT = "idempotent"


@dataclass(frozen=True)
class Game:
    """One YouTube broadcast = one tracking context (MVP)."""

    id: UUID
    platform: str
    external_broadcast_id: str
    channel_id: str | None
    tracking_status: TrackingStatus
    trusted_left_score: int | None
    trusted_right_score: int | None
    score_revision: int
    last_confirmed_at: datetime | None
    last_confirmed_timestamp_s: float | None
    last_confirmed_frame_index: int | None
    last_confirmed_epoch: int | None
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    platform_lifecycle: PlatformLifecycle = PlatformLifecycle.UNKNOWN
    scheduled_start_at: datetime | None = None
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    last_synced_at: datetime | None = None
    tracking_enabled: bool = True
    last_tracking_error: str | None = None
    last_worker_finished_at: datetime | None = None
    confirmed_left_team_id: str | None = None
    confirmed_right_team_id: str | None = None
    matchup_revision: int = 0
    matchup_confirmed_at: datetime | None = None
    matchup_confirmed_timestamp_s: float | None = None
    matchup_confirmed_frame_index: int | None = None
    matchup_confirmed_epoch: int | None = None
    matchup_left_method: str | None = None
    matchup_right_method: str | None = None
    matchup_conflict_count: int = 0
    last_matchup_conflict: str | None = None

    @property
    def pair(self) -> tuple[int, int] | None:
        if self.trusted_left_score is None or self.trusted_right_score is None:
            return None
        return (self.trusted_left_score, self.trusted_right_score)

    def trusted_state(self) -> TrustedScoreState:
        """Snapshot for M7 restore. Runtime observation cursors stay empty."""
        initialized = self.score_revision > 0
        return TrustedScoreState(
            left_score=self.trusted_left_score,
            right_score=self.trusted_right_score,
            initialized=initialized,
            revision=self.score_revision,
            last_confirmed_timestamp_s=self.last_confirmed_timestamp_s,
            last_confirmed_frame_index=self.last_confirmed_frame_index,
            last_confirmed_epoch=self.last_confirmed_epoch,
            frozen=self.tracking_status is TrackingStatus.ACQUISITION_ENDED,
        )

    def matchup_state(self) -> ConfirmedMatchupState:
        initialized = self.matchup_revision > 0
        conflict_pair = None
        raw = self.last_matchup_conflict
        if raw and "/" in raw:
            left, _, right = raw.partition("/")
            if left and right and "/" not in right:
                conflict_pair = (left, right)
        return ConfirmedMatchupState(
            left_team_id=self.confirmed_left_team_id,
            right_team_id=self.confirmed_right_team_id,
            initialized=initialized,
            revision=self.matchup_revision,
            last_confirmed_timestamp_s=self.matchup_confirmed_timestamp_s,
            last_confirmed_frame_index=self.matchup_confirmed_frame_index,
            last_confirmed_epoch=self.matchup_confirmed_epoch,
            conflict_count=self.matchup_conflict_count,
            last_conflict_pair=conflict_pair,
            frozen=self.tracking_status is TrackingStatus.ACQUISITION_ENDED,
        )


@dataclass(frozen=True)
class ScoreChangeRecord:
    """Confirmed score history row plus wall-clock persist time for the API."""

    change: ConfirmedScoreChange
    persisted_at: datetime


@dataclass(frozen=True)
class PersistResult:
    outcome: PersistOutcome
    game: Game
    change: ConfirmedScoreChange


@dataclass(frozen=True)
class MatchupPersistResult:
    outcome: PersistOutcome
    game: Game
    change: ConfirmedMatchupChange
