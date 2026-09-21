"""Live Stream Game Center - backend video ingestion and scoreboard calibration.

Ingestion (`lsgc.resolver`, `lsgc.decoder`) turns a YouTube URL into `Frame`
objects without a browser. Calibration (`lsgc.calibration`) records where a
given channel draws its scoreboard. Nothing here performs detection or OCR yet.
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
