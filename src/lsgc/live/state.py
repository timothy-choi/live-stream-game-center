"""In-memory health snapshot for a live acquisition session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SessionState(str, Enum):
    """Lifecycle of one continuous acquisition session.

    Terminal states: ENDED, FAILED, CANCELLED. STARTING / RUNNING /
    RECOVERING are in-flight.
    """

    STARTING = "starting"
    RUNNING = "running"
    RECOVERING = "recovering"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (SessionState.ENDED, SessionState.FAILED, SessionState.CANCELLED)


@dataclass(frozen=True)
class SessionHealth:
    """Point-in-time diagnostics. Safe to print: no media URLs, no headers."""

    state: SessionState
    watch_url: str
    """The stable YouTube watch URL, not a resolved CDN URL."""

    source_id: str | None
    interval_s: float
    frames_emitted: int
    frames_skipped: int
    """Duplicates / out-of-order samples dropped after reconnect."""

    resolve_attempts: int
    reconnect_count: int
    """Times a working connection was replaced (EOF or decode failure after
    at least one successful connection)."""

    consecutive_failures: int
    source_epoch: int
    """Increments each time a newly resolved source begins decoding."""

    last_frame_at: datetime | None
    last_pts_s: float | None
    last_error: str | None
    """Sanitized; never contains a signed media URL."""

    started_at: datetime
    ended_at: datetime | None
    longest_gap_s: float | None
    last_gap_s: float | None

    def summary(self) -> str:
        gap = "n/a" if self.longest_gap_s is None else f"{self.longest_gap_s:.2f}s"
        error = self.last_error or "-"
        return (
            f"state={self.state.value} id={self.source_id or '-'} "
            f"emitted={self.frames_emitted} skipped={self.frames_skipped} "
            f"resolves={self.resolve_attempts} reconnects={self.reconnect_count} "
            f"failures={self.consecutive_failures} epoch={self.source_epoch} "
            f"longest_gap={gap} last_error={error}"
        )
