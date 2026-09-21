"""Live Stream Game Center - backend video ingestion and scoreboard calibration.

Ingestion turns a YouTube URL into `Frame` objects without a browser.
`lsgc.live` keeps that pipeline open for one live broadcast. Calibration
records where a channel draws its scoreboard; vision classifies presence.
`lsgc.score` reads numeric scores from calibrated crops as raw observations,
not trusted game state.
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
