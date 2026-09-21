"""Calibration error types.

Kept separate from `lsgc.errors`, which is scoped to frame ingestion. A bad
calibration profile is a configuration problem, not an acquisition or decoding
problem, and callers handle it differently.
"""


class CalibrationError(Exception):
    """Base class for calibration failures."""


class InvalidRegionError(CalibrationError):
    """A rectangle is not expressible under the coordinate convention."""


class CalibrationValidationError(CalibrationError):
    """A profile's geometry or metadata is internally inconsistent."""


class ProfileSchemaError(CalibrationError):
    """A serialized profile is malformed or uses an unsupported schema version."""


class ChannelMismatchError(CalibrationError):
    """A profile was used for a channel it was not calibrated against.

    Calibration is per-channel; treating one channel's profile as a global
    default is the mistake this exists to prevent.
    """
