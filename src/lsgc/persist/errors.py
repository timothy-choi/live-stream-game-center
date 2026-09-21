"""Persistence errors.

Separate from ingestion, vision, OCR, and M7 validation: a database problem
is not a bad frame and not an unconfirmed candidate.
"""


class PersistError(Exception):
    """Base class for durable score-state failures."""


class ConfigError(PersistError):
    """Database configuration is missing or invalid."""


class SchemaError(PersistError):
    """Migration or schema invariant failed."""


class UnknownGameError(PersistError):
    """No durable game exists for the requested identity."""


class StaleConfirmationError(PersistError):
    """A confirmation older than the persisted revision was rejected."""


class ConflictingConfirmationError(PersistError):
    """Same revision with different payload, or a skipped revision."""
