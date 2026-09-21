"""One acquisition worker: LiveFrameSession -> M6 pipeline -> DurableScoreTracker.

Does not reimplement M4 reconnect, M7 confirmation, or M8 SQL.
"""

from __future__ import annotations

from collections.abc import Callable

from lsgc.broadcast.calibration import ChannelCalibration
from lsgc.broadcast.models import WorkerOutcome, youtube_watch_url
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.live.errors import SessionFailed
from lsgc.live.session import LiveFrameSession
from lsgc.live.state import SessionState
from lsgc.models import Frame
from lsgc.persist.models import PLATFORM_YOUTUBE, Game
from lsgc.persist.tracker import DurableScoreTracker
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.vision.detector import ScoreboardDetector
from lsgc.score.reader import ScoreReader

SessionFactory = Callable[[str], object]
PipelineFactory = Callable[[ChannelCalibration], ScoreObservationPipeline]
TrackerFactory = Callable[[Game], DurableScoreTracker]


def default_session_factory(
    watch_url: str, *, interval_s: float = 2.0
) -> LiveFrameSession:
    return LiveFrameSession(watch_url, interval_s=interval_s)


def default_pipeline_factory(calibration: ChannelCalibration) -> ScoreObservationPipeline:
    detector = ScoreboardDetector(calibration.profile, calibration.reference)
    reader = ScoreReader(calibration.glyphs)
    return ScoreObservationPipeline(detector, reader)


def default_tracker_factory(store: object, game: Game) -> DurableScoreTracker:
    return DurableScoreTracker.open(
        store,
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id=game.external_broadcast_id,
        channel_id=game.channel_id,
    )


class TrackingWorker:
    """Runs on a supervisor thread. Cancel is cooperative via session.cancel()."""

    def __init__(
        self,
        game: Game,
        *,
        calibration: ChannelCalibration,
        store: object,
        session_factory: SessionFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
        tracker_factory: TrackerFactory | None = None,
        frame_interval_s: float = 2.0,
    ) -> None:
        self.broadcast_id = game.external_broadcast_id
        self._game = game
        self._calibration = calibration
        self._store = store
        self._session_factory = session_factory
        self._pipeline_factory = pipeline_factory or default_pipeline_factory
        self._tracker_factory = tracker_factory
        self._frame_interval_s = frame_interval_s
        self._session: object | None = None
        self.frames_emitted = 0
        self.last_error: str | None = None
        self.outcome: WorkerOutcome | None = None

    def cancel(self) -> None:
        session = self._session
        if session is not None and hasattr(session, "cancel"):
            session.cancel()

    def run(self) -> WorkerOutcome:
        try:
            return self._run()
        except Exception as exc:
            self.last_error = sanitize_control_plane_text(str(exc))
            self.outcome = WorkerOutcome.FAILED
            return WorkerOutcome.FAILED

    def _run(self) -> WorkerOutcome:
        watch_url = youtube_watch_url(self.broadcast_id)
        if self._session_factory is None:
            session = default_session_factory(
                watch_url, interval_s=self._frame_interval_s
            )
        else:
            session = self._session_factory(watch_url)
        self._session = session
        if self._tracker_factory is None:
            tracker = default_tracker_factory(self._store, self._game)
        else:
            tracker = self._tracker_factory(self._game)
        pipeline = self._pipeline_factory(self._calibration)
        try:
            for frame in session.frames():
                self.frames_emitted += 1
                self._consume(tracker, pipeline, frame)
        except SessionFailed as exc:
            self.last_error = sanitize_control_plane_text(str(exc))
            self.outcome = WorkerOutcome.FAILED
            return WorkerOutcome.FAILED
        finally:
            if hasattr(session, "cancel"):
                session.cancel()

        health = session.health if hasattr(session, "health") else None
        state = health.state if health is not None else getattr(
            session, "state", SessionState.CANCELLED
        )
        if state is SessionState.ENDED:
            self.outcome = WorkerOutcome.ACQUISITION_ENDED
            return WorkerOutcome.ACQUISITION_ENDED
        if state is SessionState.FAILED:
            self.last_error = sanitize_control_plane_text(
                getattr(health, "last_error", None) or "acquisition failed"
            )
            self.outcome = WorkerOutcome.FAILED
            return WorkerOutcome.FAILED
        self.outcome = WorkerOutcome.CANCELLED
        return WorkerOutcome.CANCELLED

    def _consume(
        self,
        tracker: DurableScoreTracker,
        pipeline: ScoreObservationPipeline,
        frame: Frame,
    ) -> None:
        observation = pipeline.observe_frame(frame)
        if observation is None:
            tracker.note_absence(
                frame_index=frame.index,
                timestamp_s=frame.timestamp_s,
                source_epoch=_source_epoch(frame),
            )
            return
        tracker.apply(observation)


def _source_epoch(frame: Frame) -> int | None:
    extra = frame.extra or {}
    value = extra.get("source_epoch")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
