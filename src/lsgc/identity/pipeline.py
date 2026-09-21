"""Detector-gated matchup observation. Independent of score reading."""

from __future__ import annotations

from lsgc.identity.models import MatchupObservation
from lsgc.identity.recognizer import IdentityRecognizer
from lsgc.models import Frame
from lsgc.vision.detector import DetectionResult, ScoreboardDetector


class MatchupObservationPipeline:
    """M3 presence check then independent left/right identity reads."""

    def __init__(
        self, detector: ScoreboardDetector, recognizer: IdentityRecognizer
    ) -> None:
        recognizer.profile.assert_channel(detector.profile.channel_id)
        self.detector = detector
        self.recognizer = recognizer

    def observe_with_detection(
        self, frame: Frame, detection: DetectionResult
    ) -> MatchupObservation | None:
        if not detection.present:
            return None
        left, right = self.recognizer.recognize_image(frame.image)
        extra = frame.extra or {}
        epoch = extra.get("source_epoch")
        return MatchupObservation(
            frame_index=frame.index,
            timestamp_s=frame.timestamp_s,
            received_at=frame.received_at,
            left=left,
            right=right,
            detection_present=True,
            detection_score=detection.score,
            detection_threshold=detection.threshold,
            channel_id=detection.channel_id,
            profile_id=detection.profile_id,
            profile_revision=detection.profile_revision,
            frame_width=detection.frame_width,
            frame_height=detection.frame_height,
            source_id=frame.source_id,
            source_url=frame.source_url,
            source_epoch=epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None,
        )

    def observe_frame(self, frame: Frame) -> MatchupObservation | None:
        detection = self.detector.detect_frame(frame)
        return self.observe_with_detection(frame, detection)
