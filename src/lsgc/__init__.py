"""Live Stream Game Center - backend video ingestion and scoreboard calibration.

Ingestion turns a YouTube URL into `Frame` objects without a browser.
`lsgc.live` keeps that pipeline open for one live broadcast. Calibration
records where a channel draws its scoreboard; vision classifies presence.
`lsgc.score` reads numeric scores from calibrated crops as raw observations.
`lsgc.track` confirms those observations over time into trusted visual score
state. Trusted is not official, final, or persisted.
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
