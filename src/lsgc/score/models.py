"""Observation-only score-reading results.

A `RawScoreObservation` is a claim about what one frame *appears* to show in
the calibrated score crops. It is not trusted game state, not a confirmed
score change, and not an official score. Later milestones own temporal
confirmation and authoritative state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class UnreadableReason(str, Enum):
    """Why a side did not yield a numeric value.

    These are diagnostic classifications, not football rules.
    """

    EMPTY_CROP = "empty_crop"
    NO_DIGITS = "no_digits"
    TOO_MANY_DIGITS = "too_many_digits"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True)
class ScoreRead:
    """One side's numeric read, or an explicit unreadable outcome.

    `confidence` and `margin` are quality heuristics derived from
    zero-normalised correlation against digit glyphs. They are not calibrated
    probabilities.
    """

    value: int | None
    readable: bool
    confidence: float | None
    """Minimum per-digit ZNCC in [-1, 1] when a match was attempted."""

    margin: float | None
    """Minimum gap between the best and second-best glyph across digits."""

    raw_digits: str
    """Concatenated best-matching digit characters, empty when none."""

    reason: UnreadableReason | None = None

    def __post_init__(self) -> None:
        if self.readable:
            if self.value is None:
                raise ValueError("A readable ScoreRead must carry an integer value.")
            if self.value < 0:
                raise ValueError(f"Score values are non-negative, got {self.value}")
            if self.reason is not None:
                raise ValueError("A readable ScoreRead cannot also carry a reason.")
        else:
            if self.value is not None:
                raise ValueError("An unreadable ScoreRead must not carry a value.")
            if self.reason is None:
                raise ValueError("An unreadable ScoreRead needs a reason.")

    @classmethod
    def of(
        cls,
        value: int,
        *,
        confidence: float,
        margin: float,
        raw_digits: str,
    ) -> ScoreRead:
        return cls(
            value=value,
            readable=True,
            confidence=confidence,
            margin=margin,
            raw_digits=raw_digits,
            reason=None,
        )

    @classmethod
    def unreadable(
        cls,
        reason: UnreadableReason,
        *,
        confidence: float | None = None,
        margin: float | None = None,
        raw_digits: str = "",
    ) -> ScoreRead:
        return cls(
            value=None,
            readable=False,
            confidence=confidence,
            margin=margin,
            raw_digits=raw_digits,
            reason=reason,
        )

    def summary(self) -> str:
        if self.readable:
            conf = "n/a" if self.confidence is None else f"{self.confidence:.3f}"
            return f"{self.value} readable conf={conf}"
        conf = "n/a" if self.confidence is None else f"{self.confidence:.3f}"
        return f"unreadable ({self.reason.value}) conf={conf}"


@dataclass(frozen=True)
class RawScoreObservation:
    """What one frame visually appears to show for left and right scores.

    Left and right are independent: one side can be readable while the other
    is not. This object never means "the game's official score is now these
    values."
    """

    frame_index: int | None
    timestamp_s: float | None
    received_at: datetime | None
    left: ScoreRead
    right: ScoreRead
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

    @property
    def both_readable(self) -> bool:
        return self.left.readable and self.right.readable

    def summary(self) -> str:
        left = self.left.summary()
        right = self.right.summary()
        return (
            f"RAW OBSERVATION  L={left}  R={right}\n"
            f"  detection PRESENT score={self.detection_score:+.3f} "
            f"threshold={self.detection_threshold:.2f}\n"
            f"  frame {self.frame_width}x{self.frame_height} idx={self.frame_index} "
            f"t={self.timestamp_s} profile {self.profile_id} r{self.profile_revision}"
        )
