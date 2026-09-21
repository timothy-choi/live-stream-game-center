"""Detector-gated score observation.

Normal production-style flow:

1. receive a Frame (or BGR image);
2. run the existing M3 `ScoreboardDetector`;
3. if ABSENT, do not attempt score reading — return None;
4. if PRESENT, scale calibrated score regions and read each side independently.

Score OCR is never used to decide whether the scoreboard exists. M3 owns that.
"""

from __future__ import annotations

import numpy as np

from lsgc.models import Frame
from lsgc.score.models import RawScoreObservation
from lsgc.score.reader import ScoreReader
from lsgc.vision.detector import ScoreboardDetector


class ScoreObservationPipeline:
    """Presence check then raw score observation. No trusted state."""

    def __init__(self, detector: ScoreboardDetector, reader: ScoreReader) -> None:
        reader.glyphs.assert_matches(detector.profile)
        self.detector = detector
        self.reader = reader

    def observe(self, image: np.ndarray) -> RawScoreObservation | None:
        detection = self.detector.detect(image)
        if not detection.present:
            return None
        left, right = self.reader.read_image(image, self.detector.profile)
        return RawScoreObservation(
            frame_index=None,
            timestamp_s=None,
            received_at=None,
            left=left,
            right=right,
            detection_present=detection.present,
            detection_score=detection.score,
            detection_threshold=detection.threshold,
            channel_id=detection.channel_id,
            profile_id=detection.profile_id,
            profile_revision=detection.profile_revision,
            frame_width=detection.frame_width,
            frame_height=detection.frame_height,
        )

    def observe_frame(self, frame: Frame) -> RawScoreObservation | None:
        detection = self.detector.detect_frame(frame)
        if not detection.present:
            return None
        left, right = self.reader.read_image(frame.image, self.detector.profile)
        return RawScoreObservation(
            frame_index=frame.index,
            timestamp_s=frame.timestamp_s,
            received_at=frame.received_at,
            left=left,
            right=right,
            detection_present=detection.present,
            detection_score=detection.score,
            detection_threshold=detection.threshold,
            channel_id=detection.channel_id,
            profile_id=detection.profile_id,
            profile_revision=detection.profile_revision,
            frame_width=detection.frame_width,
            frame_height=detection.frame_height,
            source_id=frame.source_id,
            source_url=frame.source_url,
            source_epoch=_source_epoch(frame),
        )


def _source_epoch(frame: Frame) -> int | None:
    value = frame.extra.get("source_epoch") if frame.extra else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None
