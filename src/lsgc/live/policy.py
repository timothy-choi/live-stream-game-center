"""Bounded retry/backoff for live acquisition recovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """How the session reacts to a failed resolve or decode attempt.

    Consecutive failures count toward `max_attempts`. A successfully emitted
    frame resets the counter. Backoff is exponential and capped; there is no
    infinite tight loop.
    """

    max_attempts: int = 8
    """Stop and enter FAILED after this many consecutive unsuccessful attempts."""

    initial_backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    multiplier: float = 2.0

    def backoff_s(self, consecutive_failures: int) -> float:
        """Seconds to wait before attempt `consecutive_failures` (1-based)."""
        if consecutive_failures < 1:
            return 0.0
        delay = self.initial_backoff_s * (self.multiplier ** (consecutive_failures - 1))
        return min(self.max_backoff_s, delay)

    def exhausted(self, consecutive_failures: int) -> bool:
        return consecutive_failures >= self.max_attempts
