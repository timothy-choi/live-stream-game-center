"""Read left/right postgame scores from profile-configured regions.

Reuses M6's deterministic glyph matcher. Coordinates always come from the
FinalizationProfile, never from gameplay scoreboard geometry.
"""

from __future__ import annotations

import numpy as np

from lsgc.finalize.profile import FinalizationProfile
from lsgc.score.models import ScoreRead
from lsgc.score.preprocess import extract_region
from lsgc.score.reader import ScoreReader


def read_final_score(
    image: np.ndarray,
    profile: FinalizationProfile,
    reader: ScoreReader,
) -> tuple[ScoreRead, ScoreRead]:
    """Return (left, right) raw reads for this profile's final-score regions."""
    height, width = image.shape[:2]
    fitted = profile.for_frame_size(width, height)
    left_crop = extract_region(image, fitted.left_final_score_region)
    right_crop = extract_region(image, fitted.right_final_score_region)
    return reader.read_crop(left_crop), reader.read_crop(right_crop)
