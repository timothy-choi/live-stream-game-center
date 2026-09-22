"""Trusted score snapshots, candidates, confirmed changes, apply outcomes.

TRUSTED means: temporally confirmed visual score.
It does not mean official, final, persisted, or manually verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScoreProvenance(str, Enum):
    """How a confirmed pair was observed. Append-only history distinguishes these."""

    GAMEPLAY_SCOREBOARD = "GAMEPLAY_SCOREBOARD"
    RECOVERED_GAMEPLAY_SCOREBOARD = "RECOVERED_GAMEPLAY_SCOREBOARD"
    POSTGAME_FINAL = "POSTGAME_FINAL"


class ApplyOutcome(str, Enum):
    """What the validator did with one input. Diagnostic, not a probability."""

    CONFIRMED = "confirmed"
    CANDIDATE_STARTED = "candidate_started"
    CANDIDATE_ACCUMULATED = "candidate_accumulated"
    CANDIDATE_REPLACED = "candidate_replaced"
    CANDIDATE_RESET = "candidate_reset"
    MATCHES_TRUSTED = "matches_trusted"
    IGNORED_PARTIAL = "ignored_partial"
    IGNORED_UNREADABLE = "ignored_unreadable"
    IGNORED_DUPLICATE = "ignored_duplicate"
    IGNORED_STALE = "ignored_stale"
    ABSENT = "absent"
    DISCONTINUITY = "discontinuity"
    FROZEN = "frozen"
    RESET = "reset"


@dataclass(frozen=True)
class TrustedScoreState:
    """Current temporally confirmed pair, or uninitialized.

    `left_score` / `right_score` are None until the first confirmation.
    There is no 0-0 default. `frozen` means acquisition ended; the pair is
    preserved and is not a game-final.
    """

    left_score: int | None
    right_score: int | None
    initialized: bool
    revision: int
    """Increments on every confirmation, including initialization. 0 before any."""

    last_confirmed_timestamp_s: float | None = None
    last_confirmed_frame_index: int | None = None
    last_confirmed_epoch: int | None = None
    last_observation_timestamp_s: float | None = None
    last_observation_frame_index: int | None = None
    last_observation_epoch: int | None = None
    frozen: bool = False

    @property
    def pair(self) -> tuple[int, int] | None:
        if self.left_score is None or self.right_score is None:
            return None
        return (self.left_score, self.right_score)

    def summary(self) -> str:
        if not self.initialized:
            pair = "uninitialized"
        else:
            pair = f"{self.left_score}-{self.right_score}"
        frozen = " frozen" if self.frozen else ""
        return f"TRUSTED {pair} revision={self.revision}{frozen}"


@dataclass(frozen=True)
class CandidateScore:
    """A proposed pair accumulating consecutive consistent evidence."""

    left: int
    right: int
    evidence_count: int
    first_timestamp_s: float | None
    first_frame_index: int | None
    first_epoch: int | None
    last_timestamp_s: float | None
    last_frame_index: int | None
    last_epoch: int | None

    @property
    def pair(self) -> tuple[int, int]:
        return (self.left, self.right)

    def summary(self) -> str:
        return (
            f"CANDIDATE {self.left}-{self.right} evidence={self.evidence_count} "
            f"first_t={self.first_timestamp_s} last_t={self.last_timestamp_s}"
        )


@dataclass(frozen=True)
class ConfirmedScoreChange:
    """One confirmed transition of trusted state.

    M7 holds this in memory. M8 persists the same fields. It is still not an
    official, final, or corrected score.
    """

    revision: int
    previous_left: int | None
    previous_right: int | None
    new_left: int
    new_right: int
    evidence_count: int
    first_seen_timestamp_s: float | None
    first_seen_frame_index: int | None
    confirmed_timestamp_s: float | None
    confirmed_frame_index: int | None
    confirmed_epoch: int | None
    initialization: bool
    source: ScoreProvenance = ScoreProvenance.GAMEPLAY_SCOREBOARD

    @property
    def previous_pair(self) -> tuple[int, int] | None:
        if self.previous_left is None or self.previous_right is None:
            return None
        return (self.previous_left, self.previous_right)

    @property
    def new_pair(self) -> tuple[int, int]:
        return (self.new_left, self.new_right)

    def summary(self) -> str:
        if self.initialization:
            prev = "None"
            kind = "INIT"
        else:
            prev = f"{self.previous_left}-{self.previous_right}"
            kind = "CHANGE"
        return (
            f"CONFIRMED {kind} {prev} -> {self.new_left}-{self.new_right} "
            f"revision={self.revision} evidence={self.evidence_count}"
        )


@dataclass(frozen=True)
class ApplyResult:
    """Deterministic result of one validator input."""

    outcome: ApplyOutcome
    state: TrustedScoreState
    candidate: CandidateScore | None
    change: ConfirmedScoreChange | None = None

    def summary(self) -> str:
        lines = [self.outcome.value, self.state.summary()]
        if self.candidate is not None:
            lines.append(self.candidate.summary())
        if self.change is not None:
            lines.append(self.change.summary())
        return " ".join(lines)
