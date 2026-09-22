"""Profile-driven completed-game screen detection.

The detector answers only: do THIS profile's stable anchors match the frame?
It does not know which video game produced the image. Score regions, team
artwork, and other dynamic_ignore rectangles are not used as structure.
"""

from __future__ import annotations

from enum import Enum

import numpy as np

from lsgc.finalize.profile import FinalizationProfile
from lsgc.models import Frame
from lsgc.vision.detector import (
    DEFAULT_THRESHOLD,
    AnchorEvidence,
    DetectionResult,
    _gradient_magnitude,
    _zero_normalised_correlation,
)
from lsgc.vision.errors import ReferenceMismatchError
from lsgc.vision.reference import ScoreboardReference, extract_descriptor


class CompletionPresence(str, Enum):
    POSTGAME_ABSENT = "POSTGAME_ABSENT"
    POSTGAME_PRESENT = "POSTGAME_PRESENT"


class CompletionDetector:
    """Detects one channel's calibrated completed-game screen in a frame."""

    def __init__(
        self,
        profile: FinalizationProfile,
        reference: ScoreboardReference,
        *,
        threshold: float | None = None,
        expect_channel: str | None = None,
    ) -> None:
        reference.assert_matches(profile)
        if expect_channel is not None:
            profile.assert_channel(expect_channel)
        chosen = profile.presence_threshold if threshold is None else threshold
        if not -1.0 <= chosen <= 1.0:
            raise ValueError(f"threshold must lie in [-1, 1], got {chosen}")
        self.profile = profile
        self.reference = reference
        self.threshold = chosen
        self._reference_gradients = tuple(
            _gradient_magnitude(anchor.patch) for anchor in reference.anchors
        )
        self._scaled: dict[tuple[int, int], FinalizationProfile] = {}

    def _profile_for(self, width: int, height: int) -> FinalizationProfile:
        key = (width, height)
        if key not in self._scaled:
            self._scaled[key] = self.profile.for_frame_size(width, height)
        return self._scaled[key]

    def detect(self, image: np.ndarray) -> DetectionResult:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ReferenceMismatchError(
                f"Expected an HxWx3 BGR image, got shape {image.shape}"
            )
        height, width = image.shape[:2]
        fitted = self._profile_for(width, height)
        evidence = []
        for anchor_reference, reference_gradient, region in zip(
            self.reference.anchors, self._reference_gradients, fitted.stable_anchors
        ):
            probe = extract_descriptor(image, region)
            evidence.append(
                AnchorEvidence(
                    index=anchor_reference.index,
                    region=region,
                    intensity_similarity=_zero_normalised_correlation(
                        probe, anchor_reference.patch
                    ),
                    structure_similarity=_zero_normalised_correlation(
                        _gradient_magnitude(probe), reference_gradient
                    ),
                )
            )
        score = min(anchor.score for anchor in evidence)
        return DetectionResult(
            present=score >= self.threshold,
            score=score,
            threshold=self.threshold,
            anchors=tuple(evidence),
            frame_width=width,
            frame_height=height,
            channel_id=self.profile.channel_id,
            profile_id=self.profile.profile_id,
            profile_revision=self.profile.revision,
        )

    def detect_frame(self, frame: Frame) -> DetectionResult:
        return self.detect(frame.image)

    def presence(self, image: np.ndarray) -> CompletionPresence:
        result = self.detect(image)
        if result.present:
            return CompletionPresence.POSTGAME_PRESENT
        return CompletionPresence.POSTGAME_ABSENT


def detect_completion(
    image: np.ndarray,
    profile: FinalizationProfile,
    reference: ScoreboardReference,
    *,
    threshold: float | None = None,
    expect_channel: str | None = None,
) -> DetectionResult:
    detector = CompletionDetector(
        profile, reference, threshold=threshold, expect_channel=expect_channel
    )
    return detector.detect(image)


DEFAULT_COMPLETION_THRESHOLD = DEFAULT_THRESHOLD
