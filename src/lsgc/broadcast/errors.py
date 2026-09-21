"""Broadcast discovery / orchestration errors. Not ingestion errors."""

from __future__ import annotations

from lsgc.broadcast.sanitize import sanitize_control_plane_text


class BroadcastError(Exception):
    """Control-plane failure. Does not imply a broadcast ended."""


class YouTubeApiError(BroadcastError):
    """YouTube Data API request failed. Durable state must stay intact."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
    ) -> None:
        cleaned = sanitize_control_plane_text(message) or "YouTube API error"
        super().__init__(cleaned)
        self.status = status
        self.reason = reason


class BroadcastConfigError(BroadcastError):
    """Missing channel / credential / calibration-dir configuration."""


class CalibrationNotReadyError(BroadcastError):
    """Channel has no usable calibration. Do not substitute another channel."""
