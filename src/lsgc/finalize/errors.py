"""Finalization calibration and runtime errors."""


class FinalizationError(Exception):
    """Base class for final-screen calibration/runtime failures."""


class FinalizationValidationError(FinalizationError):
    """A finalization profile's geometry or metadata is inconsistent."""
