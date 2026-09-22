"""Calibrated scoreboard-presence detection.

Consumes frame imagery plus a channel's calibration, and answers one question:
is this channel's calibrated scoreboard on screen? No OCR, no score reading, no
team recognition, and no cross-frame state.

Knows nothing of yt-dlp or FFmpeg, keeping the
`YouTube -> resolver -> MediaSource -> decoder -> Frame -> vision` boundary.
Score reading lives in `lsgc.score` and is gated on this detector.
"""

from lsgc.vision.detector import (
    DEFAULT_THRESHOLD,
    AnchorEvidence,
    DetectionResult,
    ScoreboardDetector,
    detect_scoreboard,
)
from lsgc.vision.errors import ReferenceMismatchError, ReferenceSchemaError, VisionError
from lsgc.vision.overlay import render_detection, save_detection
from lsgc.vision.reference import (
    DESCRIPTOR_HEIGHT,
    DESCRIPTOR_WIDTH,
    REFERENCE_SCHEMA_VERSION,
    AnchorLayout,
    AnchorReference,
    ScoreboardReference,
    build_reference,
    extract_descriptor,
    load_reference,
    load_reference_unchecked,
    reference_from_dict,
    reference_to_dict,
    save_reference,
)

__all__ = [
    "DEFAULT_THRESHOLD",
    "DESCRIPTOR_HEIGHT",
    "DESCRIPTOR_WIDTH",
    "REFERENCE_SCHEMA_VERSION",
    "AnchorEvidence",
    "AnchorLayout",
    "AnchorReference",
    "DetectionResult",
    "ReferenceMismatchError",
    "ReferenceSchemaError",
    "ScoreboardDetector",
    "ScoreboardReference",
    "VisionError",
    "build_reference",
    "detect_scoreboard",
    "extract_descriptor",
    "load_reference",
    "load_reference_unchecked",
    "reference_from_dict",
    "reference_to_dict",
    "render_detection",
    "save_detection",
    "save_reference",
]
