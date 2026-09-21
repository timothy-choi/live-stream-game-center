"""Numeric score reader for one calibrated score crop.

Reads the current image independently. It does not know about previous frames,
football scoring rules, teams, clocks, or timestamps. A single read is never
treated as a confirmed score change.
"""

from __future__ import annotations

import numpy as np

from lsgc.calibration.profile import CalibrationProfile
from lsgc.score.glyphs import GLYPH_HEIGHT, GLYPH_WIDTH, DigitGlyphSet
from lsgc.score.models import ScoreRead, UnreadableReason
from lsgc.score.preprocess import (
    digit_boxes,
    extract_score_crops,
    glyph_patch,
    localisation_mask,
    min_channel,
    upscale_gray,
)

DEFAULT_MIN_SCORE = 0.80
"""Minimum per-digit zero-normalised correlation to accept a match.

This is a quality heuristic, not a calibrated probability. On the M6
construction frames every true digit match scored ≥ 0.94; hard-negative crops
from menus and TOUCHDOWN banners that the reader was *forced* to look at
topped out around 0.66. 0.80 sits in that gap. Held-out validation confirmed
the same operating point without per-video retuning.
"""

DEFAULT_MIN_MARGIN = 0.08
"""Minimum ZNCC gap between the best and second-best digit.

Rejects a read when two glyphs are nearly tied. Construction/held-out true
matches had a minimum gap of ~0.12.
"""

MAX_VALUE = 199
MAX_DIGITS = 3
"""Sanity bounds for OCR garbage, not football rules.

The Arcade scoreboard in this capture shows 1–2 digits in a ~59x26 crop.
Three digit boxes or a value above 199 is treated as unreadable rather than
silently coerced. Values that would be unusual *as football scores* (1, 4, a
decrease, a jump of 14, …) are accepted if the pixels support them.
"""


class ScoreReader:
    """Reads left and right numeric scores from calibrated crops."""

    def __init__(
        self,
        glyphs: DigitGlyphSet,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
        min_margin: float = DEFAULT_MIN_MARGIN,
        expect_channel: str | None = None,
    ) -> None:
        if not -1.0 <= min_score <= 1.0:
            raise ValueError(f"min_score must lie in [-1, 1], got {min_score}")
        if min_margin < 0:
            raise ValueError(f"min_margin must be >= 0, got {min_margin}")
        if expect_channel is not None:
            glyphs.assert_channel(expect_channel)
        self.glyphs = glyphs
        self.min_score = min_score
        self.min_margin = min_margin

    def read_crop(self, crop: np.ndarray) -> ScoreRead:
        """Read one score crop. Safe on empty, noisy, or non-score imagery."""
        if crop is None or crop.size == 0 or crop.ndim != 3:
            return ScoreRead.unreadable(UnreadableReason.EMPTY_CROP)
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            return ScoreRead.unreadable(UnreadableReason.EMPTY_CROP)

        upscaled = upscale_gray(min_channel(crop))
        boxes = digit_boxes(localisation_mask(upscaled))
        if not boxes:
            return ScoreRead.unreadable(UnreadableReason.NO_DIGITS)
        if len(boxes) > MAX_DIGITS:
            return ScoreRead.unreadable(UnreadableReason.TOO_MANY_DIGITS)

        digits: list[str] = []
        confidences: list[float] = []
        margins: list[float] = []
        for box in boxes:
            patch = glyph_patch(
                upscaled, box, width=GLYPH_WIDTH, height=GLYPH_HEIGHT
            )
            if patch is None:
                return ScoreRead.unreadable(UnreadableReason.NO_DIGITS)
            digit, score, margin = self.glyphs.match(patch)
            digits.append(str(digit))
            confidences.append(score)
            margins.append(margin)
            if score < self.min_score:
                return ScoreRead.unreadable(
                    UnreadableReason.LOW_CONFIDENCE,
                    confidence=score,
                    margin=margin,
                    raw_digits="".join(digits),
                )
            if margin < self.min_margin:
                return ScoreRead.unreadable(
                    UnreadableReason.AMBIGUOUS,
                    confidence=score,
                    margin=margin,
                    raw_digits="".join(digits),
                )

        raw = "".join(digits)
        value = int(raw)
        confidence = min(confidences)
        margin = min(margins)
        if value > MAX_VALUE:
            return ScoreRead.unreadable(
                UnreadableReason.OUT_OF_RANGE,
                confidence=confidence,
                margin=margin,
                raw_digits=raw,
            )
        return ScoreRead.of(
            value, confidence=confidence, margin=margin, raw_digits=raw
        )

    def read_image(
        self, image: np.ndarray, profile: CalibrationProfile
    ) -> tuple[ScoreRead, ScoreRead]:
        """Read both sides from a full frame using calibrated score regions.

        Does not consult the detector. Callers that care about scoreboard
        presence should use `ScoreObservationPipeline`.
        """
        self.glyphs.assert_matches(profile)
        left_crop, right_crop, _fitted = extract_score_crops(image, profile)
        return self.read_crop(left_crop), self.read_crop(right_crop)
