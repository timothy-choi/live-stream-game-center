"""YouTube Data API v3 control-plane client.

DATA PLANE (M4) remains yt-dlp + FFmpeg. This module never resolves playable
media URLs and never scrapes HTML.

Official HTTP:
  GET https://www.googleapis.com/youtube/v3/search
  GET https://www.googleapis.com/youtube/v3/videos
  GET https://www.googleapis.com/youtube/v3/liveBroadcasts  (OAuth, mine=true)

Quota (verified from Google's quota calculator / method docs, not guessed):
  - search.list: own daily bucket, default 100 calls/day, 1 per HTTP call.
    Each paginated page is another call.
  - videos.list: 1 unit per HTTP call (up to 50 ids).
  - liveBroadcasts.list: consumes Data API quota; exact unit cost is not
    claimed here because the liveBroadcasts.list doc page does not state it.
    Prefer it over search.list when an OAuth token is available.

A missing search hit is not treated as ENDED. Refresh known LIVE/UPCOMING
ids with videos.list when they are absent from the current discovery set.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import certifi

from lsgc.broadcast.errors import BroadcastConfigError, YouTubeApiError
from lsgc.broadcast.models import DiscoveredBroadcast, PlatformLifecycle
from lsgc.broadcast.sanitize import sanitize_control_plane_text

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
SEARCH_MAX_RESULTS = 50
VIDEOS_MAX_IDS = 50

JsonObject = dict[str, Any]
FetchPage = Callable[[str | None], JsonObject]


class JsonTransport(Protocol):
    def get_json(
        self,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> JsonObject: ...


class BroadcastCatalog(Protocol):
    def list_channel_broadcasts(
        self, channel_id: str
    ) -> tuple[DiscoveredBroadcast, ...]: ...

    def refresh_videos(
        self, video_ids: Sequence[str]
    ) -> tuple[DiscoveredBroadcast, ...]: ...


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def lifecycle_from_video_item(item: Mapping[str, Any]) -> PlatformLifecycle:
    """Map videos.list snippet + liveStreamingDetails to platform lifecycle."""
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    details = (
        item.get("liveStreamingDetails")
        if isinstance(item.get("liveStreamingDetails"), dict)
        else {}
    )
    content = str(snippet.get("liveBroadcastContent") or "none").strip().lower()
    if details.get("actualEndTime"):
        return PlatformLifecycle.ENDED
    if content == "live":
        return PlatformLifecycle.LIVE
    if content == "upcoming":
        return PlatformLifecycle.UPCOMING
    if details.get("actualStartTime") and not details.get("actualEndTime"):
        return PlatformLifecycle.LIVE
    if content in {"none", "completed"} and details.get("actualStartTime"):
        return PlatformLifecycle.ENDED
    if details.get("scheduledStartTime") and not details.get("actualStartTime"):
        return PlatformLifecycle.UPCOMING
    return PlatformLifecycle.UNKNOWN


def lifecycle_from_live_broadcast_item(item: Mapping[str, Any]) -> PlatformLifecycle | None:
    """Map liveBroadcasts.list status.lifeCycleStatus.

    testing / testStarting are not public audience broadcasts; skip them.
    """
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    raw = str(status.get("lifeCycleStatus") or "").strip().lower()
    if raw in {"live", "livestarting"}:
        return PlatformLifecycle.LIVE
    if raw in {"ready", "created"}:
        return PlatformLifecycle.UPCOMING
    if raw in {"complete", "revoked"}:
        return PlatformLifecycle.ENDED
    if raw in {"testing", "teststarting"}:
        return None
    return PlatformLifecycle.UNKNOWN


def discovered_from_video_item(item: Mapping[str, Any]) -> DiscoveredBroadcast | None:
    video_id = _video_id(item)
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    channel_id = str(snippet.get("channelId") or "").strip()
    if not video_id or not channel_id:
        return None
    details = (
        item.get("liveStreamingDetails")
        if isinstance(item.get("liveStreamingDetails"), dict)
        else {}
    )
    title = str(snippet.get("title") or "").strip() or None
    return DiscoveredBroadcast(
        broadcast_id=video_id,
        channel_id=channel_id,
        lifecycle=lifecycle_from_video_item(item),
        title=title,
        scheduled_start=parse_rfc3339(
            _first_str(details.get("scheduledStartTime"), snippet.get("scheduledStartTime"))
        ),
        actual_start=parse_rfc3339(
            _first_str(details.get("actualStartTime"), snippet.get("actualStartTime"))
        ),
        actual_end=parse_rfc3339(
            _first_str(details.get("actualEndTime"), snippet.get("actualEndTime"))
        ),
    )


def discovered_from_live_broadcast_item(
    item: Mapping[str, Any],
) -> DiscoveredBroadcast | None:
    broadcast_id = str(item.get("id") or "").strip()
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    channel_id = str(snippet.get("channelId") or "").strip()
    lifecycle = lifecycle_from_live_broadcast_item(item)
    if not broadcast_id or not channel_id or lifecycle is None:
        return None
    title = str(snippet.get("title") or "").strip() or None
    return DiscoveredBroadcast(
        broadcast_id=broadcast_id,
        channel_id=channel_id,
        lifecycle=lifecycle,
        title=title,
        scheduled_start=parse_rfc3339(snippet.get("scheduledStartTime")),
        actual_start=parse_rfc3339(snippet.get("actualStartTime")),
        actual_end=parse_rfc3339(snippet.get("actualEndTime")),
    )


def collect_paginated_items(fetch_page: FetchPage) -> list[JsonObject]:
    """Follow nextPageToken until absent. Do not use pageInfo.totalResults."""
    items: list[JsonObject] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        body = fetch_page(token)
        chunk = body.get("items") or []
        if isinstance(chunk, list):
            items.extend(item for item in chunk if isinstance(item, dict))
        next_token = body.get("nextPageToken")
        if not next_token:
            break
        token = str(next_token)
        if token in seen_tokens:
            break
        seen_tokens.add(token)
    return items


class UrllibJsonTransport:
    """HTTPS GET via urllib. Never logs the request URL (it may contain key=)."""

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._context = ssl.create_default_context(cafile=certifi.where())

    def get_json(
        self,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> JsonObject:
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value}
        )
        url = f"{YOUTUBE_API_BASE}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_s, context=self._context
            ) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            reason = _api_reason(raw)
            message = sanitize_control_plane_text(
                f"YouTube API HTTP {exc.code}: {reason or raw[:200]}"
            ) or "YouTube API HTTP error"
            raise YouTubeApiError(message, status=exc.code, reason=reason) from None
        except urllib.error.URLError as exc:
            message = sanitize_control_plane_text(
                f"YouTube API transport error: {exc.reason}"
            ) or "YouTube API transport error"
            raise YouTubeApiError(message) from None
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise YouTubeApiError("YouTube API returned non-JSON") from exc
        if not isinstance(body, dict):
            raise YouTubeApiError("YouTube API returned a non-object JSON payload")
        if "error" in body:
            reason = _api_reason(payload)
            message = sanitize_control_plane_text(
                f"YouTube API error: {reason or 'unknown'}"
            ) or "YouTube API error"
            raise YouTubeApiError(message, reason=reason)
        return body


class YouTubeDataApiClient:
    """Production catalog. Tests should inject a fake BroadcastCatalog instead."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        access_token: str | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        key = (api_key or "").strip() or None
        token = (access_token or "").strip() or None
        if not key and not token:
            raise BroadcastConfigError(
                "YouTube Data API needs LSGC_YOUTUBE_API_KEY or "
                "LSGC_YOUTUBE_ACCESS_TOKEN"
            )
        self._api_key = key
        self._access_token = token
        self._transport = transport or UrllibJsonTransport()

    def list_channel_broadcasts(
        self, channel_id: str
    ) -> tuple[DiscoveredBroadcast, ...]:
        wanted = (channel_id or "").strip()
        if not wanted:
            raise YouTubeApiError("channel_id must not be empty")
        discovered = (
            self._list_via_live_broadcasts()
            if self._access_token
            else self._list_via_search(wanted)
        )
        return tuple(
            item
            for item in discovered
            if item.channel_id == wanted
            and item.lifecycle is not PlatformLifecycle.UNKNOWN
        )

    def list_authorized_channel(self) -> tuple[str | None, str | None]:
        """channels.list mine=true. Returns the OAuth-selected channel only."""
        body = self._get("/channels", {"part": "id,snippet", "mine": "true"})
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            return None, None
        first = items[0]
        if not isinstance(first, dict):
            return None, None
        channel_id = str(first.get("id") or "").strip() or None
        snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
        title = str(snippet.get("title") or "").strip() or None
        return channel_id, title

    def refresh_videos(
        self, video_ids: Sequence[str]
    ) -> tuple[DiscoveredBroadcast, ...]:
        ids = [item.strip() for item in video_ids if item and item.strip()]
        if not ids:
            return ()
        return tuple(self._videos(ids))

    def _list_via_search(self, channel_id: str) -> list[DiscoveredBroadcast]:
        ids: list[str] = []
        seen: set[str] = set()
        for event_type in ("live", "upcoming"):
            for item in collect_paginated_items(
                lambda token, et=event_type: self._get(
                    "/search",
                    {
                        "part": "snippet",
                        "channelId": channel_id,
                        "eventType": et,
                        "type": "video",
                        "maxResults": str(SEARCH_MAX_RESULTS),
                        "order": "date",
                        **({"pageToken": token} if token else {}),
                    },
                )
            ):
                video_id = _video_id(item)
                if video_id and video_id not in seen:
                    seen.add(video_id)
                    ids.append(video_id)
        return self._videos(ids)

    def _list_via_live_broadcasts(self) -> list[DiscoveredBroadcast]:
        """Authenticated owner listing.

        Official liveBroadcasts.list: specify exactly one of broadcastStatus,
        id, or mine. Combining mine=true with broadcastStatus returns HTTP 400
        incompatibleParameters. Use mine=true, then keep upcoming/live only so
        completed history is not imported.
        https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/list
        """
        items = collect_paginated_items(
            lambda token: self._get(
                "/liveBroadcasts",
                {
                    "part": "id,snippet,status",
                    "mine": "true",
                    "maxResults": "50",
                    **({"pageToken": token} if token else {}),
                },
            )
        )
        discovered: list[DiscoveredBroadcast] = []
        seen: set[str] = set()
        for item in items:
            record = discovered_from_live_broadcast_item(item)
            if record is None or record.broadcast_id in seen:
                continue
            if record.lifecycle not in {
                PlatformLifecycle.UPCOMING,
                PlatformLifecycle.LIVE,
            }:
                continue
            seen.add(record.broadcast_id)
            discovered.append(record)
        return discovered

    def _videos(self, video_ids: Sequence[str]) -> list[DiscoveredBroadcast]:
        discovered: list[DiscoveredBroadcast] = []
        unique: list[str] = []
        seen: set[str] = set()
        for video_id in video_ids:
            if video_id not in seen:
                seen.add(video_id)
                unique.append(video_id)
        for offset in range(0, len(unique), VIDEOS_MAX_IDS):
            batch = unique[offset : offset + VIDEOS_MAX_IDS]
            body = self._get(
                "/videos",
                {
                    "part": "snippet,liveStreamingDetails",
                    "id": ",".join(batch),
                    "maxResults": str(VIDEOS_MAX_IDS),
                },
            )
            for item in body.get("items") or []:
                if not isinstance(item, dict):
                    continue
                record = discovered_from_video_item(item)
                if record is not None:
                    discovered.append(record)
        return discovered

    def _get(self, path: str, params: dict[str, str]) -> JsonObject:
        headers: dict[str, str] = {"Accept": "application/json"}
        query = dict(params)
        if self._api_key:
            query["key"] = self._api_key
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return self._transport.get_json(path, query, headers)


def _video_id(item: Mapping[str, Any]) -> str:
    raw = item.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        video_id = str(raw.get("videoId") or "").strip()
        if video_id:
            return video_id
    return ""


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _api_reason(payload: str) -> str | None:
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        errors = error.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("reason"):
                return str(first["reason"])
        if error.get("message"):
            return str(error["message"])
    return None
