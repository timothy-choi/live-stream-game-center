"""Live Stream Game Center - backend video ingestion.

Milestone 1 scope: prove that backend Python code can obtain and decode frames
from YouTube without a browser. Nothing here knows about scoreboards or OCR.
"""

from lsgc.errors import DecodeError, IngestionError, SourceResolutionError
from lsgc.models import Frame, MediaSource

__all__ = [
    "DecodeError",
    "Frame",
    "IngestionError",
    "MediaSource",
    "SourceResolutionError",
]
