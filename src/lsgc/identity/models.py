"""Raw identity observations, matchup observations, and confirmed matchup.

A raw logo/name read is never a confirmed matchup. Confirmed matchup is
temporally validated visual scoreboard identity — not schedule truth, not
title-derived truth, and not owner-verified truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class IdentityMethod(str, Enum):
    TEXT_NAME = "text_name"
    TEXT_ABBREVIATION = "text_abbreviation"
    LOGO = "logo"
    UNKNOWN = "unknown"


class UnreadableIdentityReason(str, Enum):
    EMPTY_CROP = "empty_crop"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"
    TEXT_UNVALIDATED = "text_unvalidated"
    NO_TEMPLATE = "no_template"
    SAME_TEAM = "same_team"


class MatchupApplyOutcome(str, Enum):
    CONFIRMED = "confirmed"
    CANDIDATE_STARTED = "candidate_started"
    CANDIDATE_ACCUMULATED = "candidate_accumulated"
    CANDIDATE_REPLACED = "candidate_replaced"
    CANDIDATE_RESET = "candidate_reset"
    MATCHES_CONFIRMED = "matches_confirmed"
    CONFLICT = "conflict"
    IGNORED_PARTIAL = "ignored_partial"
    IGNORED_UNREADABLE = "ignored_unreadable"
    IGNORED_INVALID = "ignored_invalid"
    IGNORED_DUPLICATE = "ignored_duplicate"
    IGNORED_STALE = "ignored_stale"
    ABSENT = "absent"
    DISCONTINUITY = "discontinuity"
    FROZEN = "frozen"


@dataclass(frozen=True)
class TeamIdentityObservation:
    """What one identity crop appears to show. Not confirmed."""

    team_id: str | None
    method: IdentityMethod
    readable: bool
    confidence: float | None
    """Best template similarity in [-1, 1]. Heuristic, not a probability."""

    margin: float | None
    """Gap from best to second-best team. None when incomparable."""

    reason: UnreadableIdentityReason | None = None
    runner_up_team_id: str | None = None

    def __post_init__(self) -> None:
        if self.readable:
            if not self.team_id:
                raise ValueError("A readable identity observation needs a team_id")
            if self.reason is not None:
                raise ValueError("A readable identity observation cannot carry a reason")
        else:
            if self.team_id is not None:
                raise ValueError("An unreadable identity observation must not carry a team_id")
            if self.reason is None:
                raise ValueError("An unreadable identity observation needs a reason")

    @classmethod
    def known(
        cls,
        team_id: str,
        *,
        method: IdentityMethod,
        confidence: float,
        margin: float | None,
        runner_up_team_id: str | None = None,
    ) -> TeamIdentityObservation:
        return cls(
            team_id=team_id,
            method=method,
            readable=True,
            confidence=confidence,
            margin=margin,
            runner_up_team_id=runner_up_team_id,
        )

    @classmethod
    def unknown(
        cls,
        reason: UnreadableIdentityReason,
        *,
        method: IdentityMethod = IdentityMethod.UNKNOWN,
        confidence: float | None = None,
        margin: float | None = None,
        runner_up_team_id: str | None = None,
    ) -> TeamIdentityObservation:
        return cls(
            team_id=None,
            method=method,
            readable=False,
            confidence=confidence,
            margin=margin,
            reason=reason,
            runner_up_team_id=runner_up_team_id,
        )


@dataclass(frozen=True)
class MatchupObservation:
    """Left + right identity from one scoreboard-present frame. Not confirmed."""

    frame_index: int | None
    timestamp_s: float | None
    received_at: datetime | None
    left: TeamIdentityObservation
    right: TeamIdentityObservation
    detection_present: bool
    detection_score: float
    detection_threshold: float
    channel_id: str
    profile_id: str
    profile_revision: int
    frame_width: int
    frame_height: int
    source_id: str | None = None
    source_url: str | None = None
    source_epoch: int | None = None

    @property
    def both_readable(self) -> bool:
        return self.left.readable and self.right.readable

    @property
    def pair(self) -> tuple[str, str] | None:
        if not self.both_readable:
            return None
        assert self.left.team_id is not None and self.right.team_id is not None
        return (self.left.team_id, self.right.team_id)

    @property
    def is_valid_complete(self) -> bool:
        pair = self.pair
        return pair is not None and pair[0] != pair[1]


@dataclass(frozen=True)
class ConfirmedMatchupState:
    left_team_id: str | None
    right_team_id: str | None
    initialized: bool
    revision: int
    last_confirmed_timestamp_s: float | None = None
    last_confirmed_frame_index: int | None = None
    last_confirmed_epoch: int | None = None
    conflict_count: int = 0
    last_conflict_pair: tuple[str, str] | None = None
    frozen: bool = False

    @property
    def pair(self) -> tuple[str, str] | None:
        if self.left_team_id is None or self.right_team_id is None:
            return None
        return (self.left_team_id, self.right_team_id)

    def summary(self) -> str:
        pair = "uninitialized" if not self.initialized else f"{self.left_team_id}/{self.right_team_id}"
        extra = f" conflicts={self.conflict_count}" if self.conflict_count else ""
        return f"CONFIRMED MATCHUP {pair} revision={self.revision}{extra}"


@dataclass(frozen=True)
class CandidateMatchup:
    left_team_id: str
    right_team_id: str
    evidence_count: int
    first_timestamp_s: float | None
    first_frame_index: int | None
    first_epoch: int | None
    last_timestamp_s: float | None
    last_frame_index: int | None
    last_epoch: int | None

    @property
    def pair(self) -> tuple[str, str]:
        return (self.left_team_id, self.right_team_id)


@dataclass(frozen=True)
class ConfirmedMatchupChange:
    """One durable matchup confirmation. MVP: initialization only, never rewrite."""

    revision: int
    left_team_id: str
    right_team_id: str
    left_method: IdentityMethod
    right_method: IdentityMethod
    evidence_count: int
    first_seen_timestamp_s: float | None
    first_seen_frame_index: int | None
    confirmed_timestamp_s: float | None
    confirmed_frame_index: int | None
    confirmed_epoch: int | None
    initialization: bool

    @property
    def pair(self) -> tuple[str, str]:
        return (self.left_team_id, self.right_team_id)


@dataclass(frozen=True)
class MatchupApplyResult:
    outcome: MatchupApplyOutcome
    state: ConfirmedMatchupState
    candidate: CandidateMatchup | None
    change: ConfirmedMatchupChange | None
    conflict_pair: tuple[str, str] | None = None
