"""Broadcast catalog that prefers durable OAuth connections.

Developer API-key/channel-id configuration is a fallback for channels that
are not OAuth-connected. The same channel is never synced twice.
"""

from __future__ import annotations

from collections.abc import Sequence

from lsgc.broadcast.errors import YouTubeApiError
from lsgc.broadcast.models import ConnectedChannel, DiscoveredBroadcast
from lsgc.broadcast.youtube import YouTubeDataApiClient
from lsgc.youtube_oauth.models import ConnectionStatus
from lsgc.youtube_oauth.service import YoutubeConnectionService


class HybridBroadcastCatalog:
    def __init__(
        self,
        *,
        store,
        service: YoutubeConnectionService | None,
        developer_api_key: str | None = None,
        developer_access_token: str | None = None,
        client_factory=None,
    ) -> None:
        self._store = store
        self._service = service
        self._developer_api_key = (developer_api_key or "").strip() or None
        self._developer_access_token = (developer_access_token or "").strip() or None
        self._client_factory = client_factory or YouTubeDataApiClient

    def list_channel_broadcasts(self, channel_id: str) -> tuple[DiscoveredBroadcast, ...]:
        wanted = (channel_id or "").strip()
        if not wanted:
            raise YouTubeApiError("channel_id must not be empty")
        connection = None
        if hasattr(self._store, "get_active_connection_for_channel"):
            connection = self._store.get_active_connection_for_channel(wanted)
        if (
            connection is not None
            and connection.status is ConnectionStatus.CONNECTED
            and self._service is not None
        ):
            token = self._service.access_token_for(connection)
            client = self._client_factory(access_token=token)
            found = client.list_channel_broadcasts(wanted)
            return tuple(item for item in found if item.channel_id == wanted)
        if self._developer_api_key or self._developer_access_token:
            client = self._client_factory(
                api_key=self._developer_api_key,
                access_token=self._developer_access_token,
            )
            return client.list_channel_broadcasts(wanted)
        raise YouTubeApiError("no YouTube authorization for this channel")

    def refresh_videos(self, video_ids: Sequence[str]) -> tuple[DiscoveredBroadcast, ...]:
        connection = None
        if hasattr(self._store, "list_active_youtube_connections"):
            active = self._store.list_active_youtube_connections()
            connection = active[0] if active else None
        if connection is not None and self._service is not None:
            token = self._service.access_token_for(connection)
            client = self._client_factory(access_token=token)
            return client.refresh_videos(video_ids)
        if self._developer_api_key or self._developer_access_token:
            client = self._client_factory(
                api_key=self._developer_api_key,
                access_token=self._developer_access_token,
            )
            return client.refresh_videos(video_ids)
        return ()


def merge_sync_channels(store, developer_channels: Sequence[ConnectedChannel]) -> tuple[ConnectedChannel, ...]:
    """OAuth CONNECTED channels first. Developer static ids fill gaps only."""
    oauth: list[ConnectedChannel] = []
    seen: set[str] = set()
    if hasattr(store, "list_active_youtube_connections"):
        for connection in store.list_active_youtube_connections():
            if connection.youtube_channel_id in seen:
                continue
            seen.add(connection.youtube_channel_id)
            oauth.append(
                ConnectedChannel(
                    channel_id=connection.youtube_channel_id,
                    display_name=connection.youtube_channel_title,
                )
            )
    extras = [
        channel
        for channel in developer_channels
        if channel.channel_id not in seen
    ]
    return tuple(oauth + extras)
