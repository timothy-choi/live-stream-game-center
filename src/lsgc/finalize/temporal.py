"""Temporal confirmation for completion presence and postgame numeric scores.

One frame cannot establish completion or a final score. Defaults match M7:
at least two consistent observations spanning at least 2.0 seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lsgc.finalize.policy import FinalizationPolicy
from lsgc.score.models import RawScoreObservation
from lsgc.track.models import ApplyResult, ConfirmedScoreChange
from lsgc.track.policy import ConfirmationPolicy
from lsgc.track.validator import TemporalScoreValidator


class CompletionEvidence(str, Enum):
    NO_COMPLETION_EVIDENCE = "NO_COMPLETION_EVIDENCE"
    COMPLETION_CANDIDATE = "COMPLETION_CANDIDATE"
    COMPLETION_CONFIRMED = "COMPLETION_CONFIRMED"


@dataclass
class CompletionConfirmer:
    """Accumulate consecutive POSTGAME_PRESENT observations."""

    policy: FinalizationPolicy
    _count: int = 0
    _first_ts: float | None = None
    _first_index: int | None = None
    _last_ts: float | None = None
    _last_index: int | None = None
    _confirmed: bool = False

    def restore_confirmed(self) -> None:
        """Durably recorded completion does not need to be rediscovered."""
        self._confirmed = True

    @property
    def confirmed(self) -> bool:
        return self._confirmed

    def observe(
        self,
        present: bool,
        *,
        timestamp_s: float | None,
        frame_index: int | None,
    ) -> CompletionEvidence:
        if self._confirmed:
            return CompletionEvidence.COMPLETION_CONFIRMED
        if not present:
            self._count = 0
            self._first_ts = None
            self._first_index = None
            self._last_ts = None
            self._last_index = None
            return CompletionEvidence.NO_COMPLETION_EVIDENCE
        if self._count == 0:
            self._count = 1
            self._first_ts = timestamp_s
            self._first_index = frame_index
            self._last_ts = timestamp_s
            self._last_index = frame_index
            return CompletionEvidence.COMPLETION_CANDIDATE
        self._count += 1
        self._last_ts = timestamp_s
        self._last_index = frame_index
        if self._ready():
            self._confirmed = True
            return CompletionEvidence.COMPLETION_CONFIRMED
        return CompletionEvidence.COMPLETION_CANDIDATE

    def _ready(self) -> bool:
        if self._count < self.policy.required_observations:
            return False
        if self._first_ts is not None and self._last_ts is not None:
            return (self._last_ts - self._first_ts) >= self.policy.min_span_s
        if self._first_index is not None and self._last_index is not None:
            return (self._last_index - self._first_index) >= (
                self.policy.required_observations - 1
            )
        return False


class PostgameScoreConfirmer:
    """Independent temporal confirmer for postgame numeric pairs.

    Isolated from gameplay trusted state. A confirmed pair is evidence that
    the completed-game screen showed those digits, not yet a durable update.
    """

    def __init__(self, policy: FinalizationPolicy | None = None) -> None:
        confirmation = ConfirmationPolicy(
            required_observations=(policy or FinalizationPolicy()).required_observations,
            min_span_s=(policy or FinalizationPolicy()).min_span_s,
        )
        self._validator = TemporalScoreValidator(confirmation)

    def apply(self, observation: RawScoreObservation) -> ApplyResult:
        return self._validator.apply(observation)

    def confirmed_change(self, observation: RawScoreObservation) -> ConfirmedScoreChange | None:
        result = self.apply(observation)
        return result.change
