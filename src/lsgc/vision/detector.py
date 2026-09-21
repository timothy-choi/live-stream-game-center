"""Single-frame calibrated scoreboard-presence detection.

The question answered here is narrow: is THIS channel's calibrated scoreboard
on screen in this frame? Not what it says - no scores, teams, clock or OCR.

Method, chosen by measuring the M3 validation set rather than by habit. For
each stable anchor the frame's crop is put on the descriptor grid and compared
with the stored reference patch two ways:

- intensity similarity: zero-normalised cross-correlation of the grayscale
  patches, which ignores overall brightness and contrast changes;
- structure similarity: the same correlation on Sobel gradient magnitude,
  which responds to edge layout and, being unsigned, survives a panel behind
  the divider changing shade.

An anchor scores the lower of the two, and the frame scores the lowest anchor.
Taking minima throughout means a frame is only accepted when every anchor
matches on both measures - deliberately conservative, because a false positive
would later feed unrelated graphics to score reading. The M1 TOUCHDOWN banner
is exactly that risk: it reuses the scoreboard band and its own divider chrome,
so it clears a "something is there" test but not this one.

Frame-at-a-time only. Debouncing across consecutive frames belongs to a
runtime milestone, and would hide how good single-frame detection really is.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from lsgc.calibration.geometry import Region
from lsgc.calibration.profile import CalibrationProfile
from lsgc.models import Frame
from lsgc.vision.errors import ReferenceMismatchError
from lsgc.vision.reference import ScoreboardReference, extract_descriptor

DEFAULT_THRESHOLD = 0.55
"""Score at or above which the calibrated scoreboard is considered present.

Measured on the 40-sample M3 validation set at 1280x720: positives ran
0.831..0.999 and negatives -0.077..-0.077 or lower, so any cut in
(-0.08, 0.83) separates them perfectly. 0.55 sits inside that gap, leaving
0.28 of headroom below the weakest observed positive and 0.63 above the
strongest negative. It is placed above the midpoint on purpose: false
positives are the costlier error here. Real 1920x1080 frames of the same
broadcast scored 0.961+ positive and -0.02 or lower negative against the same
720p-derived reference, so no per-resolution tuning is needed.

Validated against one channel's calibration. It is not a universal Madden
constant, and another channel needs its own measurement.
"""


def _zero_normalised_correlation(probe: np.ndarray, reference: np.ndarray) -> float:
    """Correlation in [-1, 1], insensitive to offset and scale in brightness.

    A flat patch has no variation to correlate, so it scores 0 rather than
    dividing by zero - which is the right answer: a blank region is no
    evidence that the calibrated structure is present.
    """
    a = probe.astype(np.float32)
    b = reference.astype(np.float32)
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denominator < 1e-6:
        return 0.0
    return float(np.clip((a * b).sum() / denominator, -1.0, 1.0))


def _gradient_magnitude(patch: np.ndarray) -> np.ndarray:
    values = patch.astype(np.float32)
    return cv2.magnitude(
        cv2.Sobel(values, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(values, cv2.CV_32F, 0, 1, ksize=3),
    )


@dataclass(frozen=True)
class AnchorEvidence:
    """Why one anchor did or did not support the decision."""

    index: int
    region: Region
    """Where this anchor was inspected, in the frame's own coordinates - which
    differs from the profile's reference coordinates on a rescaled frame."""

    intensity_similarity: float
    structure_similarity: float

    @property
    def score(self) -> float:
        return min(self.intensity_similarity, self.structure_similarity)

    def passed(self, threshold: float) -> bool:
        return self.score >= threshold

    def __str__(self) -> str:
        return (
            f"anchor[{self.index}] {self.region} score={self.score:+.3f} "
            f"(intensity {self.intensity_similarity:+.3f}, "
            f"structure {self.structure_similarity:+.3f})"
        )


@dataclass(frozen=True)
class DetectionResult:
    """Structured outcome of one frame's presence check."""

    present: bool
    score: float
    """Lowest anchor score, in [-1, 1]. Compared against `threshold`."""

    threshold: float
    anchors: tuple[AnchorEvidence, ...]

    frame_width: int
    frame_height: int
    channel_id: str
    profile_id: str
    profile_revision: int

    @property
    def margin(self) -> float:
        """Distance from the decision boundary; negative when absent."""
        return self.score - self.threshold

    @property
    def weakest_anchor(self) -> AnchorEvidence:
        """The anchor that decided the outcome."""
        return min(self.anchors, key=lambda a: a.score)

    def summary(self) -> str:
        verdict = "PRESENT" if self.present else "ABSENT"
        lines = [
            f"{verdict}  score={self.score:+.3f} threshold={self.threshold:.2f} "
            f"margin={self.margin:+.3f}",
            f"  frame {self.frame_width}x{self.frame_height}, profile "
            f"{self.profile_id} r{self.profile_revision}, channel {self.channel_id}",
        ]
        lines += [f"  {anchor}" for anchor in self.anchors]
        return "\n".join(lines)


class ScoreboardDetector:
    """Detects one channel's calibrated scoreboard in individual frames.

    Profile and reference are bound at construction and checked against each
    other once, so a mismatched pair cannot reach a per-frame call. Geometry
    rescaled for a given frame size is cached, since a run of frames from one
    source is all the same size.
    """

    def __init__(
        self,
        profile: CalibrationProfile,
        reference: ScoreboardReference,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        expect_channel: str | None = None,
    ) -> None:
        reference.assert_matches(profile)
        if expect_channel is not None:
            profile.assert_channel(expect_channel)
        if not -1.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must lie in [-1, 1], got {threshold}")

        self.profile = profile
        self.reference = reference
        self.threshold = threshold
        self._reference_gradients = tuple(
            _gradient_magnitude(anchor.patch) for anchor in reference.anchors
        )
        self._scaled: dict[tuple[int, int], CalibrationProfile] = {}

    def _profile_for(self, width: int, height: int) -> CalibrationProfile:
        key = (width, height)
        if key not in self._scaled:
            # Raises for an incompatible aspect ratio rather than distorting.
            self._scaled[key] = self.profile.for_frame_size(width, height)
        return self._scaled[key]

    def detect(self, image: np.ndarray) -> DetectionResult:
        """Decide whether the calibrated scoreboard is present in one image."""
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
        """Convenience for the ingestion layer's `Frame`.

        The detector core takes a plain array; this only unwraps it, so the
        vision layer stays free of any ingestion concept beyond that.
        """
        return self.detect(frame.image)


def detect_scoreboard(
    image: np.ndarray,
    profile: CalibrationProfile,
    reference: ScoreboardReference,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    expect_channel: str | None = None,
) -> DetectionResult:
    """One-shot detection. Build a `ScoreboardDetector` for repeated frames."""
    detector = ScoreboardDetector(
        profile, reference, threshold=threshold, expect_channel=expect_channel
    )
    return detector.detect(image)
