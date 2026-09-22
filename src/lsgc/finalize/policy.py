"""Product defaults for completion confirmation, stability, and drain.

These are not video-game scoring rules. A FinalizationProfile may later
override detector/reader thresholds; temporal defaults live here so the
runtime stays evidence-driven rather than layout-specific.
"""

from __future__ import annotations

from dataclasses import dataclass

from lsgc.track.policy import M4_DEFAULT_INTERVAL_S


@dataclass(frozen=True)
class FinalizationPolicy:
    """How much visual evidence is required before FINALIZING / FINAL."""

    required_observations: int = 2
    min_span_s: float = M4_DEFAULT_INTERVAL_S
    stability_s: float = 8.0
    drain_s: float = 35.0
    recovery_window_s: float = 90.0
    """Rolling live-frame retention for retrospective rescan.

    90s covers a late-game burst of several scoreboard changes at live 2s
    sampling (~45 frames) without unbounded raw video. The value is a
    configuration knob, not a sport-specific period.
    """

    recovery_scan_interval_s: float = 0.5
    """Denser than live 2s sampling. 0.5s is 4× denser and still far from
    decoding every native frame. Stored live samples sparser than this are
    all used; seekable sources may resample at this cadence.
    """

    recovery_min_span_s: float = 0.5
    recovery_required_observations: int = 2

    @classmethod
    def from_environ(cls, environ: dict[str, str] | None = None) -> FinalizationPolicy:
        import os

        env = os.environ if environ is None else environ
        window_raw = (env.get("LSGC_RECOVERY_WINDOW_S") or "").strip()
        scan_raw = (env.get("LSGC_RECOVERY_SCAN_INTERVAL_S") or "").strip()
        window_s = float(window_raw) if window_raw else 90.0
        scan_s = float(scan_raw) if scan_raw else 0.5
        return cls(recovery_window_s=window_s, recovery_scan_interval_s=scan_s)

    def __post_init__(self) -> None:
        if self.required_observations < 2:
            raise ValueError("required_observations must be >= 2")
        if self.min_span_s < 0:
            raise ValueError("min_span_s must be >= 0")
        if self.stability_s < 0:
            raise ValueError("stability_s must be >= 0")
        if self.drain_s < 0:
            raise ValueError("drain_s must be >= 0")
        if self.recovery_window_s <= 0:
            raise ValueError("recovery_window_s must be > 0")
        if self.recovery_scan_interval_s < 0:
            raise ValueError("recovery_scan_interval_s must be >= 0")
        if self.recovery_required_observations < 2:
            raise ValueError("recovery_required_observations must be >= 2")
        if self.recovery_min_span_s < 0:
            raise ValueError("recovery_min_span_s must be >= 0")
