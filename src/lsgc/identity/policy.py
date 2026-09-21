"""Matchup confirmation policy.

Identity is more stable than a changing score, but a single frame still must
not become the durable matchup (M3 can miss a banner; a crop can be mid-fade).
Defaults follow M4's 2s developer cadence: two in-order complete pairs spanning
at least one sample interval.
"""

from __future__ import annotations

from dataclasses import dataclass

from lsgc.identity.errors import IdentityPolicyError
from lsgc.track.policy import M4_DEFAULT_INTERVAL_S


@dataclass(frozen=True)
class MatchupConfirmationPolicy:
    required_observations: int = 2
    min_span_s: float = M4_DEFAULT_INTERVAL_S
    max_candidate_gap_s: float | None = 8.0

    def __post_init__(self) -> None:
        if not isinstance(self.required_observations, int) or isinstance(
            self.required_observations, bool
        ):
            raise IdentityPolicyError("required_observations must be an int")
        if self.required_observations < 2:
            raise IdentityPolicyError(
                "required_observations must be >= 2; one frame must never "
                "become the durable matchup"
            )
        if self.min_span_s < 0 or self.min_span_s != self.min_span_s:
            raise IdentityPolicyError(f"min_span_s must be >= 0, got {self.min_span_s!r}")
        if self.max_candidate_gap_s is not None:
            if (
                self.max_candidate_gap_s <= 0
                or self.max_candidate_gap_s != self.max_candidate_gap_s
            ):
                raise IdentityPolicyError(
                    f"max_candidate_gap_s must be > 0 or None, got {self.max_candidate_gap_s!r}"
                )
