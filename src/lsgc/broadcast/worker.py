"""One acquisition worker: LiveFrameSession -> score path + identity path.

Score and identity are independent after a shared M3 presence check.
A temporary identity failure must not drop a valid score observation, and a
score-read failure must not drop valid identity evidence. Persist errors are
never swallowed.
"""

from __future__ import annotations

from collections.abc import Callable

from lsgc.broadcast.calibration import ChannelCalibration
from lsgc.broadcast.models import WorkerOutcome, youtube_watch_url
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.calibration.profile import CalibrationProfile
from lsgc.identity.logos import LogoRecognizer
from lsgc.identity.pipeline import MatchupObservationPipeline
from lsgc.identity.recognizer import IdentityRecognizer
from lsgc.live.errors import SessionFailed
from lsgc.live.session import LiveFrameSession
from lsgc.live.state import SessionState
from lsgc.models import Frame
from lsgc.persist.models import PLATFORM_YOUTUBE, Game
from lsgc.persist.tracker import DurableMatchupTracker, DurableScoreTracker
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.score.reader import ScoreReader
from lsgc.vision.detector import ScoreboardDetector

SessionFactory = Callable[[str], object]
PipelineFactory = Callable[[ChannelCalibration], ScoreObservationPipeline]
TrackerFactory = Callable[[Game], DurableScoreTracker]
IdentityPipelineFactory = Callable[[ChannelCalibration], MatchupObservationPipeline | None]
MatchupTrackerFactory = Callable[[Game], DurableMatchupTracker]


def default_session_factory(
    watch_url: str, *, interval_s: float = 2.0
) -> LiveFrameSession:
    return LiveFrameSession(watch_url, interval_s=interval_s)


def default_pipeline_factory(calibration: ChannelCalibration) -> ScoreObservationPipeline:
    detector = ScoreboardDetector(calibration.profile, calibration.reference)
    reader = ScoreReader(calibration.glyphs)
    return ScoreObservationPipeline(detector, reader)


def default_identity_pipeline_factory(
    calibration: ChannelCalibration,
) -> MatchupObservationPipeline | None:
    profile = getattr(calibration, "profile", None)
    if not isinstance(profile, CalibrationProfile):
        return None
    reference = getattr(calibration, "reference", None)
    if reference is None:
        return None
    detector = ScoreboardDetector(profile, reference)
    logos = None
    logo_set = getattr(calibration, "logos", None)
    if logo_set is not None:
        logos = LogoRecognizer(logo_set, expect_channel=profile.channel_id)
    recognizer = IdentityRecognizer(profile, logos=logos)
    return MatchupObservationPipeline(detector, recognizer)


def default_tracker_factory(store: object, game: Game) -> DurableScoreTracker:
    return DurableScoreTracker.open(
        store,
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id=game.external_broadcast_id,
        channel_id=game.channel_id,
    )


def default_matchup_tracker_factory(store: object, game: Game) -> DurableMatchupTracker:
    return DurableMatchupTracker.open(
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
        identity_pipeline_factory: IdentityPipelineFactory | None = None,
        matchup_tracker_factory: MatchupTrackerFactory | None = None,
        frame_interval_s: float = 2.0,
    ) -> None:
        self.broadcast_id = game.external_broadcast_id
        self._game = game
        self._calibration = calibration
        self._store = store
        self._session_factory = session_factory
        self._pipeline_factory = pipeline_factory or default_pipeline_factory
        self._tracker_factory = tracker_factory
        self._identity_pipeline_factory = (
            identity_pipeline_factory or default_identity_pipeline_factory
        )
        self._matchup_tracker_factory = matchup_tracker_factory
        self._frame_interval_s = frame_interval_s
        self._session: object | None = None
        self.frames_emitted = 0
        self.last_error: str | None = None
        self.outcome: WorkerOutcome | None = None
        self.identity_read_errors = 0
        self.score_read_errors = 0

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
        if self._matchup_tracker_factory is None:
            matchup_tracker = default_matchup_tracker_factory(self._store, self._game)
        else:
            matchup_tracker = self._matchup_tracker_factory(self._game)
        pipeline = self._pipeline_factory(self._calibration)
        identity_pipeline = self._identity_pipeline_factory(self._calibration)
        try:
            for frame in session.frames():
                self.frames_emitted += 1
                self._consume(tracker, matchup_tracker, pipeline, identity_pipeline, frame)
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
        matchup_tracker: DurableMatchupTracker,
        pipeline: ScoreObservationPipeline,
        identity_pipeline: MatchupObservationPipeline | None,
        frame: Frame,
    ) -> None:
        detection = None
        detector = getattr(identity_pipeline, "detector", None) if identity_pipeline else None
        if detector is None:
            detector = getattr(pipeline, "detector", None)
        if detector is not None:
            try:
                detection = detector.detect_frame(frame)
            except Exception:
                detection = None

        persist_error: Exception | None = None

        try:
            if detection is not None and hasattr(pipeline, "observe_with_detection"):
                score_obs = pipeline.observe_with_detection(frame, detection)
            else:
                score_obs = pipeline.observe_frame(frame)
        except Exception:
            self.score_read_errors += 1
            score_obs = None
            if detection is not None and not detection.present:
                tracker.note_absence(
                    frame_index=frame.index,
                    timestamp_s=frame.timestamp_s,
                    source_epoch=_source_epoch(frame),
                )
        else:
            try:
                if score_obs is None:
                    tracker.note_absence(
                        frame_index=frame.index,
                        timestamp_s=frame.timestamp_s,
                        source_epoch=_source_epoch(frame),
                    )
                else:
                    tracker.apply(score_obs)
            except Exception as exc:
                persist_error = exc

        if identity_pipeline is not None:
            try:
                if detection is not None:
                    matchup_obs = identity_pipeline.observe_with_detection(frame, detection)
                else:
                    matchup_obs = identity_pipeline.observe_frame(frame)
            except Exception:
                self.identity_read_errors += 1
                matchup_obs = None
                if detection is not None and not detection.present:
                    matchup_tracker.note_absence(
                        frame_index=frame.index,
                        timestamp_s=frame.timestamp_s,
                        source_epoch=_source_epoch(frame),
                    )
            else:
                try:
                    if matchup_obs is None:
                        matchup_tracker.note_absence(
                            frame_index=frame.index,
                            timestamp_s=frame.timestamp_s,
                            source_epoch=_source_epoch(frame),
                        )
                    else:
                        matchup_tracker.apply(matchup_obs)
                except Exception as exc:
                    persist_error = persist_error or exc

        if persist_error is not None:
            raise persist_error


def _source_epoch(frame: Frame) -> int | None:
    extra = frame.extra or {}
    value = extra.get("source_epoch")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
