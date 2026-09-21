"""Backend reconciliation loop. No viewer, REST, or frontend involved."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from lsgc.broadcast.calibration import CalibrationLookup
from lsgc.broadcast.config import BroadcastRuntimeConfig, DEFAULT_SYNC_INTERVAL_S
from lsgc.broadcast.eligibility import evaluate_eligibility
from lsgc.broadcast.models import (
    ConnectedChannel,
    EligibilityReason,
    OrchestratorStatus,
    PlatformLifecycle,
    WorkerSnapshot,
)
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.supervisor import TrackingSupervisor
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.persist.models import Game, TrackingStatus

ClockFn = Callable[[], datetime]
WaitFn = Callable[[threading.Event, float], bool]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _wait(event: threading.Event, seconds: float) -> bool:
    return event.wait(timeout=seconds)


class BroadcastOrchestrator:
    """sync configured channels, then ensure 0-or-1 worker per eligible LIVE game."""

    def __init__(
        self,
        channels: Sequence[ConnectedChannel],
        synchronizer: ChannelSynchronizer,
        supervisor: TrackingSupervisor,
        store: object,
        calibration: CalibrationLookup,
        *,
        interval_s: float = DEFAULT_SYNC_INTERVAL_S,
        clock: ClockFn = _utc_now,
        wait: WaitFn = _wait,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self._channels = tuple(channels)
        self._synchronizer = synchronizer
        self._supervisor = supervisor
        self._store = store
        self._calibration = calibration
        self._interval_s = interval_s
        self._clock = clock
        self._wait = wait
        self._status = OrchestratorStatus(
            last_sync_at=None,
            last_reconcile_at=None,
            last_sync_ok=True,
            last_sync_error=None,
            discovered_count=0,
            live_count=0,
            upcoming_count=0,
            ended_count=0,
            active_worker_count=0,
            workers=(),
        )

    @property
    def status(self) -> OrchestratorStatus:
        return self._status

    @property
    def supervisor(self) -> TrackingSupervisor:
        return self._supervisor

    def reconcile_once(self) -> OrchestratorStatus:
        """One testable iteration. Does not sleep."""
        discovered = 0
        live = 0
        upcoming = 0
        ended = 0
        any_sync_ok = True
        last_error: str | None = None
        last_sync_at = self._status.last_sync_at
        for channel in self._channels:
            report = self._synchronizer.sync_channel(channel.channel_id)
            last_sync_at = report.synced_at or last_sync_at
            if not report.ok:
                any_sync_ok = False
                last_error = report.error
                # Leave durable rows and running workers alone.
                continue
            discovered += report.discovered
            live += len(report.live_ids)
            upcoming += len(report.upcoming_ids)
            ended += len(report.ended_ids)
            games = self._store.list_games_for_channel(channel.channel_id)  # type: ignore[attr-defined]
            for game in games:
                self._reconcile_game(channel.channel_id, game)
        self._supervisor.reap()
        workers = self._supervisor.snapshots()
        self._status = OrchestratorStatus(
            last_sync_at=last_sync_at,
            last_reconcile_at=self._clock(),
            last_sync_ok=any_sync_ok,
            last_sync_error=sanitize_control_plane_text(last_error),
            discovered_count=discovered,
            live_count=live,
            upcoming_count=upcoming,
            ended_count=ended,
            active_worker_count=len(self._supervisor.active_ids()),
            workers=workers,
        )
        return self._status

    def run_forever(self, stop: threading.Event | None = None) -> None:
        halt = stop if stop is not None else threading.Event()
        try:
            while not halt.is_set():
                self.reconcile_once()
                self._wait(halt, self._interval_s)
        finally:
            self._supervisor.stop_all()

    def shutdown(self) -> None:
        self._supervisor.stop_all()

    def _reconcile_game(self, channel_id: str, game: Game) -> None:
        decision = evaluate_eligibility(
            game,
            expected_channel_id=channel_id,
            calibration=self._calibration,
        )
        active = self._supervisor.is_active(game.external_broadcast_id)
        if decision.eligible:
            self._supervisor.ensure_tracking(game)
            return
        if not active:
            if (
                game.platform_lifecycle is PlatformLifecycle.ENDED
                and game.tracking_status is TrackingStatus.TRACKING
            ):
                self._store.mark_acquisition_ended(game.id)  # type: ignore[attr-defined]
            return
        if decision.reason is EligibilityReason.TRACKING_DISABLED:
            self._supervisor.stop_tracking(game.external_broadcast_id)
            return
        if decision.reason is EligibilityReason.MISSING_CALIBRATION:
            self._supervisor.stop_tracking(game.external_broadcast_id)
            return
        if decision.reason is EligibilityReason.WRONG_CHANNEL:
            self._supervisor.stop_tracking(game.external_broadcast_id)
            return
        # NOT_LIVE while a worker is active: stop acquisition. Freeze trusted
        # score only when the platform says ENDED, not for UPCOMING.
        self._supervisor.stop_tracking(game.external_broadcast_id)
        if game.platform_lifecycle is PlatformLifecycle.ENDED:
            self._store.mark_acquisition_ended(game.id)  # type: ignore[attr-defined]

    def worker_snapshot(self, broadcast_id: str) -> WorkerSnapshot | None:
        for item in self._supervisor.snapshots():
            if item.broadcast_id == broadcast_id:
                return item
        return None


def orchestrator_from_config(
    config: BroadcastRuntimeConfig,
    *,
    store: object,
    catalog,
    calibration: CalibrationLookup,
    session_factory=None,
    pipeline_factory=None,
    tracker_factory=None,
    clock: ClockFn = _utc_now,
) -> BroadcastOrchestrator:
    from lsgc.broadcast.sync import ChannelSynchronizer as Sync
    from lsgc.broadcast.supervisor import TrackingSupervisor as Supervisor

    synchronizer = Sync(catalog, store, clock=clock)
    supervisor = Supervisor(
        store,
        calibration,
        session_factory=session_factory,
        pipeline_factory=pipeline_factory,
        tracker_factory=tracker_factory,
        clock=clock,
        retry_initial_s=config.worker_retry_initial_s,
        retry_max_s=config.worker_retry_max_s,
        frame_interval_s=config.frame_interval_s,
    )
    return BroadcastOrchestrator(
        config.channels,
        synchronizer,
        supervisor,
        store,
        calibration,
        interval_s=config.sync_interval_s,
        clock=clock,
    )
