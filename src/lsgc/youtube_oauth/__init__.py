"""YouTube account connection via Google OAuth 2.0.

This is a channel-connection boundary, not application user accounts.
Ordinary users authorize Google; they do not supply API keys or channel IDs.
"""

from lsgc.youtube_oauth.models import (
    CalibrationReadiness,
    ConnectionStatus,
    YoutubeConnection,
)

__all__ = [
    "CalibrationReadiness",
    "ConnectionStatus",
    "YoutubeConnection",
]
