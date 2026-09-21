"""Numeric score reading from calibrated scoreboard crops.

Observation layer only. A `RawScoreObservation` is what one frame appears to
show, not trusted game state. Presence is decided by `lsgc.vision`; this
package never uses score OCR to decide whether the scoreboard exists.

Knows nothing of yt-dlp or FFmpeg.
"""

from lsgc.score.errors import GlyphMismatchError, GlyphSchemaError, ScoreError
from lsgc.score.glyphs import (
    GLYPH_HEIGHT,
    GLYPH_SCHEMA_VERSION,
    GLYPH_WIDTH,
    DigitGlyphSet,
    build_glyphs,
    glyphs_from_dict,
    glyphs_to_dict,
    load_glyphs,
    load_glyphs_unchecked,
    save_glyphs,
)
from lsgc.score.models import RawScoreObservation, ScoreRead, UnreadableReason
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.score.preprocess import extract_region, extract_score_crops
from lsgc.score.reader import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    MAX_VALUE,
    ScoreReader,
)

__all__ = [
    "DEFAULT_MIN_MARGIN",
    "DEFAULT_MIN_SCORE",
    "GLYPH_HEIGHT",
    "GLYPH_SCHEMA_VERSION",
    "GLYPH_WIDTH",
    "MAX_VALUE",
    "DigitGlyphSet",
    "GlyphMismatchError",
    "GlyphSchemaError",
    "RawScoreObservation",
    "ScoreError",
    "ScoreObservationPipeline",
    "ScoreRead",
    "ScoreReader",
    "UnreadableReason",
    "build_glyphs",
    "extract_region",
    "extract_score_crops",
    "glyphs_from_dict",
    "glyphs_to_dict",
    "load_glyphs",
    "load_glyphs_unchecked",
    "save_glyphs",
]
