"""Score-reading error types.

Separate from ingestion, calibration and vision errors: a glyph-set problem is
not a decode failure, a bad profile, or a presence-detection mismatch.
"""


class ScoreError(Exception):
    """Base class for score-reading failures."""


class GlyphSchemaError(ScoreError):
    """Serialized digit glyphs are malformed or an unsupported version."""


class GlyphMismatchError(ScoreError):
    """Digit glyphs do not belong with the profile they were paired with."""
