"""Ingestion error types.

Acquisition failures and decoder failures are separate types so that callers can
tell "YouTube would not give us the media" apart from "we got media and could
not decode it". Those have different operational responses.
"""


class IngestionError(Exception):
    """Base class for frame-ingestion failures."""


class SourceResolutionError(IngestionError):
    """The YouTube source could not be resolved into playable media.

    Covers unavailable/private/invalid videos, bot checks, and signature or
    PO-token failures.
    """


class DecodeError(IngestionError):
    """Media was resolved but frames could not be decoded from it."""
