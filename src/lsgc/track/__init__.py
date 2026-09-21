"""Temporal validation of raw score observations into trusted live state.

Consumes M6 `RawScoreObservation` objects over time. A single raw read never
becomes trusted. Acquisition ending does not mark a game final.

Knows nothing of yt-dlp, FFmpeg, or databases.
"""

from lsgc.track.errors import PolicyError, RestoreError, TrackError
from lsgc.track.models import (
    ApplyOutcome,
    ApplyResult,
    CandidateScore,
    ConfirmedScoreChange,
    TrustedScoreState,
)
from lsgc.track.policy import ConfirmationPolicy, M4_DEFAULT_INTERVAL_S
from lsgc.track.validator import TemporalScoreValidator

__all__ = [
    "M4_DEFAULT_INTERVAL_S",
    "ApplyOutcome",
    "ApplyResult",
    "CandidateScore",
    "ConfirmationPolicy",
    "ConfirmedScoreChange",
    "PolicyError",
    "RestoreError",
    "TemporalScoreValidator",
    "TrackError",
    "TrustedScoreState",
]
