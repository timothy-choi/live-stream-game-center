"""Durable game / trusted-score snapshots.

Persisted trusted score = temporally confirmed visual score.
It is NOT official, corrected, final, or winner-certified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from lsgc.track.models import ConfirmedScoreChange, TrustedScoreState

PLATFORM_YOUTUBE = "youtube"


class TrackingStatus(str, Enum):
    """Acquisition/tracking lifecycle. Not a game-final."""

    TRACKING = "TRACKING"
    """Process may apply observations for this broadcast."""

    ACQUISITION_ENDED = "ACQUISITION_ENDED"
    """M4 acquisition ended. Trusted score is frozen. Not FINAL."""


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


@dataclass(frozen=True)
class PersistResult:
    outcome: PersistOutcome
    game: Game
    change: ConfirmedScoreChange
