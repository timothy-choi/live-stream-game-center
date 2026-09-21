"""Temporal score-tracking errors.

Separate from ingestion, calibration, vision and glyph errors: a policy or
validator problem is not a decode failure and not an OCR miss.
"""


class TrackError(Exception):
    """Base class for trusted-score tracking failures."""


class PolicyError(TrackError):
    """Confirmation policy is malformed."""
