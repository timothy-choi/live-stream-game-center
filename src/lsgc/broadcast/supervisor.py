"""In-process tracking supervisor: at most one local worker per broadcast id."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from lsgc.broadcast.calibration import CalibrationLookup, ChannelCalibration
from lsgc.broadcast.config import (
    DEFAULT_FRAME_INTERVAL_S,
    DEFAULT_WORKER_RETRY_INITIAL_S,
    DEFAULT_WORKER_RETRY_MAX_S,
)
from lsgc.broadcast.models import (
    WorkerOutcome,
    WorkerSnapshot,
    WorkerState,
)
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.worker import (
    IdentityPipelineFactory,
    MatchupTrackerFactory,
    PipelineFactory,
    SessionFactory,
    TrackerFactory,
    TrackingWorker,
)
from lsgc.persist.models import Game

ClockFn = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class TrackingSupervisor:
    """Zero or one worker thread per broadcast_id. Not a distributed lock."""

    def __init__(
        self,
        store: object,
        calibration: CalibrationLookup,
        *,
        session_factory: SessionFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
        tracker_factory: TrackerFactory | None = None,
        identity_pipeline_factory: IdentityPipelineFactory | None = None,
        matchup_tracker_factory: MatchupTrackerFactory | None = None,
        clock: ClockFn = _utc_now,
        retry_initial_s: float = DEFAULT_WORKER_RETRY_INITIAL_S,
        retry_max_s: float = DEFAULT_WORKER_RETRY_MAX_S,
        frame_interval_s: float = DEFAULT_FRAME_INTERVAL_S,
        thread_factory: Callable[..., threading.Thread] | None = None,
    ) -> None:
        self._store = store
        self._calibration = calibration
        self._session_factory = session_factory
        self._pipeline_factory = pipeline_factory
        self._tracker_factory = tracker_factory
        self._identity_pipeline_factory = identity_pipeline_factory
        self._matchup_tracker_factory = matchup_tracker_factory
        self._clock = clock
        self._retry_initial_s = retry_initial_s
        self._retry_max_s = retry_max_s
        self._frame_interval_s = frame_interval_s
        self._thread_factory = thread_factory or threading.Thread
        self._lock = threading.RLock()
        self._workers: dict[str, TrackingWorker] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._states: dict[str, WorkerState] = {}
        self._started_at: dict[str, datetime] = {}
        self._finished_at: dict[str, datetime] = {}
        self._last_error: dict[str, str | None] = {}
        self._retry_count: dict[str, int] = {}
        self._next_retry_at: dict[str, datetime | None] = {}
        self._frames: dict[str, int] = {}

    def active_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(
                broadcast_id
                for broadcast_id, thread in self._threads.items()
                if thread.is_alive()
            )

    def is_active(self, broadcast_id: str) -> bool:
        return broadcast_id in self.active_ids()

    def snapshots(self) -> tuple[WorkerSnapshot, ...]:
        self.reap()
        with self._lock:
            ids = set(self._states) | set(self._workers)
            return tuple(self._snapshot_locked(broadcast_id) for broadcast_id in sorted(ids))

    def ensure_tracking(
        self, game: Game, *, calibration: ChannelCalibration | None = None
    ) -> WorkerSnapshot:
        broadcast_id = game.external_broadcast_id
        self.reap()
        with self._lock:
            thread = self._threads.get(broadcast_id)
            if thread is not None and thread.is_alive():
                return self._snapshot_locked(broadcast_id)
            next_retry = self._next_retry_at.get(broadcast_id)
            now = self._clock()
            if next_retry is not None and next_retry > now:
                self._states[broadcast_id] = WorkerState.FAILED
                return self._snapshot_locked(broadcast_id)
            bundle = calibration or self._calibration.get(game.channel_id or "")
            if bundle is None:
                self._states[broadcast_id] = WorkerState.STOPPED
                self._last_error[broadcast_id] = (
                    "NOT_READY: no calibration for this channel_id"
                )
                return self._snapshot_locked(broadcast_id)
            worker = TrackingWorker(
                game,
                calibration=bundle,
                store=self._store,
                session_factory=self._session_factory,
                pipeline_factory=self._pipeline_factory,
                tracker_factory=self._tracker_factory,
                identity_pipeline_factory=self._identity_pipeline_factory,
                matchup_tracker_factory=self._matchup_tracker_factory,
                frame_interval_s=self._frame_interval_s,
            )
            self._workers[broadcast_id] = worker
            self._states[broadcast_id] = WorkerState.STARTING
            self._started_at[broadcast_id] = now
            self._finished_at.pop(broadcast_id, None)
            self._last_error[broadcast_id] = None
            thread = self._thread_factory(
                target=self._run_worker,
                args=(broadcast_id, worker),
                name=f"lsgc-track-{broadcast_id}",
                daemon=True,
            )
            self._threads[broadcast_id] = thread
            thread.start()
            if self._states.get(broadcast_id) is WorkerState.STARTING:
                self._states[broadcast_id] = WorkerState.RUNNING
            return self._snapshot_locked(broadcast_id)

    def stop_tracking(self, broadcast_id: str) -> WorkerSnapshot:
        with self._lock:
            worker = self._workers.get(broadcast_id)
            thread = self._threads.get(broadcast_id)
            if worker is not None:
                self._states[broadcast_id] = WorkerState.STOPPING
                worker.cancel()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self.reap()
        with self._lock:
            if broadcast_id in self._states and not self.is_active(broadcast_id):
                self._states[broadcast_id] = WorkerState.STOPPED
                self._next_retry_at[broadcast_id] = None
            return self._snapshot_locked(broadcast_id)

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._workers)
        for broadcast_id in ids:
            self.stop_tracking(broadcast_id)

    def reap(self) -> None:
        with self._lock:
            finished = [
                broadcast_id
                for broadcast_id, thread in list(self._threads.items())
                if not thread.is_alive()
            ]
            for broadcast_id in finished:
                self._threads.pop(broadcast_id, None)
                worker = self._workers.pop(broadcast_id, None)
                if worker is None:
                    continue
                self._frames[broadcast_id] = worker.frames_emitted
                self._finished_at[broadcast_id] = self._clock()
                error = sanitize_control_plane_text(worker.last_error)
                self._last_error[broadcast_id] = error
                outcome = worker.outcome or WorkerOutcome.STOPPED
                if outcome is WorkerOutcome.FAILED:
                    self._states[broadcast_id] = WorkerState.FAILED
                    count = self._retry_count.get(broadcast_id, 0) + 1
                    self._retry_count[broadcast_id] = count
                    delay = min(
                        self._retry_max_s,
                        self._retry_initial_s * (2 ** (count - 1)),
                    )
                    self._next_retry_at[broadcast_id] = self._clock() + timedelta(
                        seconds=delay
                    )
                elif outcome is WorkerOutcome.ACQUISITION_ENDED:
                    self._states[broadcast_id] = WorkerState.ACQUISITION_ENDED
                    self._next_retry_at[broadcast_id] = None
                    self._retry_count[broadcast_id] = 0
                else:
                    self._states[broadcast_id] = (
                        WorkerState.CANCELLED
                        if outcome is WorkerOutcome.CANCELLED
                        else WorkerState.STOPPED
                    )
                    self._next_retry_at[broadcast_id] = None
                    self._retry_count[broadcast_id] = 0
                game = self._store.get_game_by_external_id(  # type: ignore[attr-defined]
                    "youtube", broadcast_id
                )
                if game is not None:
                    self._store.record_worker_finished(  # type: ignore[attr-defined]
                        game.id, error=error, finished_at=self._finished_at[broadcast_id]
                    )

    def _run_worker(self, broadcast_id: str, worker: TrackingWorker) -> None:
        try:
            worker.run()
        except Exception as exc:
            worker.last_error = sanitize_control_plane_text(str(exc))
            worker.outcome = WorkerOutcome.FAILED

    def _snapshot_locked(self, broadcast_id: str) -> WorkerSnapshot:
        worker = self._workers.get(broadcast_id)
        thread = self._threads.get(broadcast_id)
        state = self._states.get(broadcast_id, WorkerState.STOPPED)
        if thread is not None and thread.is_alive() and state in {
            WorkerState.STARTING,
            WorkerState.RUNNING,
            WorkerState.STOPPING,
        }:
            pass
        elif thread is not None and thread.is_alive():
            state = WorkerState.RUNNING
        frames = worker.frames_emitted if worker is not None else self._frames.get(
            broadcast_id, 0
        )
        return WorkerSnapshot(
            broadcast_id=broadcast_id,
            state=state,
            started_at=self._started_at.get(broadcast_id),
            finished_at=self._finished_at.get(broadcast_id),
            last_error=self._last_error.get(broadcast_id),
            retry_count=self._retry_count.get(broadcast_id, 0),
            next_retry_at=self._next_retry_at.get(broadcast_id),
            frames_emitted=frames,
        )
