"""Confirmation policy for trusted score state.

These parameters are the only knobs the state machine uses. They are not
football rules, not per-video thresholds, and not calibrated probabilities.

Defaults assume M4's developer sampling cadence of 2.0s and M6's observation
layer (already refusing low-quality crops). They exist to reject *transient*
raw reads, not to prove a repeatedly-seen value is true.
"""

from __future__ import annotations

from dataclasses import dataclass

from lsgc.track.errors import PolicyError

M4_DEFAULT_INTERVAL_S = 2.0
"""M4 `LiveFrameSession` developer default. Not a claimed product optimum."""


@dataclass(frozen=True)
class ConfirmationPolicy:
    """How much consistent visual evidence is required to trust a pair.

    Central rule: one isolated observation cannot change trusted state.
    `required_observations` is therefore at least 2.
    """

    required_observations: int = 2
    """Distinct in-order fully-readable observations of the same pair."""

    min_span_s: float = M4_DEFAULT_INTERVAL_S
    """Minimum source-time between the first and last counted observation.

    Two frames that share a timestamp (or are closer than one sample interval)
    are not independent evidence. When timestamps are missing, distinct
    `frame_index` values satisfy the span.
    """

    max_candidate_gap_s: float | None = 8.0
    """Maximum source-time hole allowed between consecutive candidate evidence.

    Default is four missed 2s samples. A matching observation after a longer
    hole restarts the candidate at evidence=1 rather than chaining across the
    gap. `None` disables time-gap restarts (conflict and epoch still reset).
    """

    def __post_init__(self) -> None:
        if not isinstance(self.required_observations, int) or isinstance(
            self.required_observations, bool
        ):
            raise PolicyError("required_observations must be an int")
        if self.required_observations < 2:
            raise PolicyError(
                "required_observations must be >= 2; a single observation "
                "must never become trusted state."
            )
        if self.min_span_s < 0 or self.min_span_s != self.min_span_s:
            raise PolicyError(f"min_span_s must be >= 0, got {self.min_span_s!r}")
        if self.max_candidate_gap_s is not None:
            if self.max_candidate_gap_s <= 0 or self.max_candidate_gap_s != self.max_candidate_gap_s:
                raise PolicyError(
                    f"max_candidate_gap_s must be > 0 or None, got {self.max_candidate_gap_s!r}"
                )

    def summary(self) -> str:
        gap = "none" if self.max_candidate_gap_s is None else f"{self.max_candidate_gap_s:g}s"
        return (
            f"required_observations={self.required_observations} "
            f"min_span_s={self.min_span_s:g} max_candidate_gap_s={gap}"
        )
