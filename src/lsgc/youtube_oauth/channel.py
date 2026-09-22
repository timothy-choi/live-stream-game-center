"""Determine the YouTube channel selected during OAuth.

Official: GET https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true
`mine=true` returns the channel chosen in Google's OAuth picker — one identity,
not every Brand Account the Google login can access.
"""

from __future__ import annotations

from lsgc.broadcast.youtube import YouTubeDataApiClient
from lsgc.youtube_oauth.errors import YoutubeChannelLookupError


def authorized_channel(client: YouTubeDataApiClient) -> tuple[str, str | None]:
    channel_id, title = client.list_authorized_channel()
    if not channel_id:
        raise YoutubeChannelLookupError("authorized account has no YouTube channel")
    return channel_id, title
