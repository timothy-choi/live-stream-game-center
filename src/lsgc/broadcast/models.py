"""Normalized broadcast discovery records and orchestration snapshots.

YouTube API payloads stay in the client. Reconciliation works against these
types. Platform lifecycle is not tracking lifecycle, and neither is FINAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from lsgc.persist.models import PlatformLifecycle, TrackingStatus

PLATFORM_YOUTUBE = "youtube"


class WorkerState(str, Enum):
    """In-process acquisition worker. Distinct from YouTube lifecycle."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ACQUISITION_ENDED = "acquisition_ended"
    CANCELLED = "cancelled"


class EligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    NOT_LIVE = "not_live"
    TRACKING_DISABLED = "tracking_disabled"
    MISSING_CALIBRATION = "missing_calibration"
    WRONG_CHANNEL = "wrong_channel"
    ALREADY_FINAL = "already_final"


class WorkerOutcome(str, Enum):
    """Why a worker thread exited. Not a Madden game-final."""

    ACQUISITION_ENDED = "acquisition_ended"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ConnectedChannel:
    """Explicit authorization/config boundary. Never a global hardcoded id."""

    channel_id: str
    display_name: str | None = None


@dataclass(frozen=True)
class DiscoveredBroadcast:
    """Normalized catalog record. Identity is broadcast_id only."""

    broadcast_id: str
    channel_id: str
    lifecycle: PlatformLifecycle
    title: str | None = None
    scheduled_start: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason: EligibilityReason
    detail: str | None = None


@dataclass(frozen=True)
class WorkerSnapshot:
    broadcast_id: str
    state: WorkerState
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    retry_count: int
    next_retry_at: datetime | None
    frames_emitted: int = 0


@dataclass(frozen=True)
class SyncReport:
    ok: bool
    channel_id: str
    discovered: int
    upserted: int
    rejected_wrong_channel: int
    refreshed: int
    skipped_unknown: int
    error: str | None
    synced_at: datetime | None
    live_ids: tuple[str, ...]
    upcoming_ids: tuple[str, ...]
    ended_ids: tuple[str, ...]


@dataclass(frozen=True)
class OrchestratorStatus:
    last_sync_at: datetime | None
    last_reconcile_at: datetime | None
    last_sync_ok: bool
    last_sync_error: str | None
    discovered_count: int
    live_count: int
    upcoming_count: int
    ended_count: int
    active_worker_count: int
    workers: tuple[WorkerSnapshot, ...]


def youtube_watch_url(broadcast_id: str) -> str:
    """Stable watch identity for M4. Not a signed media URL."""
    identity = (broadcast_id or "").strip()
    if not identity:
        raise ValueError("broadcast_id must not be empty")
    return f"https://www.youtube.com/watch?v={identity}"


__all__ = [
    "PLATFORM_YOUTUBE",
    "ConnectedChannel",
    "DiscoveredBroadcast",
    "EligibilityDecision",
    "EligibilityReason",
    "OrchestratorStatus",
    "PlatformLifecycle",
    "SyncReport",
    "TrackingStatus",
    "WorkerOutcome",
    "WorkerSnapshot",
    "WorkerState",
    "youtube_watch_url",
]
