"""Normalized YouTube Data API parsing, pagination, and sanitization."""

from __future__ import annotations

from lsgc.broadcast.errors import YouTubeApiError
from lsgc.broadcast.models import PlatformLifecycle
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.youtube import (
    YouTubeDataApiClient,
    collect_paginated_items,
    discovered_from_live_broadcast_item,
    discovered_from_video_item,
    lifecycle_from_video_item,
    parse_rfc3339,
)
from tests.broadcast_fakes import CHANNEL_A, CHANNEL_B, QueueTransport


def test_parse_rfc3339_zulu() -> None:
    parsed = parse_rfc3339("2026-09-21T19:30:00Z")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.tzinfo is not None


def test_lifecycle_prefers_actual_end_over_live_flag() -> None:
    item = {
        "id": "abc",
        "snippet": {"channelId": CHANNEL_A, "liveBroadcastContent": "live"},
        "liveStreamingDetails": {
            "actualStartTime": "2026-09-21T18:00:00Z",
            "actualEndTime": "2026-09-21T19:00:00Z",
        },
    }
    assert lifecycle_from_video_item(item) is PlatformLifecycle.ENDED


def test_unscheduled_live_video_is_live() -> None:
    item = {
        "id": "liveNow",
        "snippet": {
            "channelId": CHANNEL_A,
            "title": "going live",
            "liveBroadcastContent": "live",
        },
        "liveStreamingDetails": {"actualStartTime": "2026-09-21T18:00:00Z"},
    }
    record = discovered_from_video_item(item)
    assert record is not None
    assert record.broadcast_id == "liveNow"
    assert record.lifecycle is PlatformLifecycle.LIVE
    assert record.scheduled_start is None


def test_upcoming_from_live_broadcast_ready() -> None:
    item = {
        "id": "sched1",
        "snippet": {
            "channelId": CHANNEL_A,
            "title": "later",
            "scheduledStartTime": "2026-09-22T01:00:00Z",
        },
        "status": {"lifeCycleStatus": "ready"},
    }
    record = discovered_from_live_broadcast_item(item)
    assert record is not None
    assert record.lifecycle is PlatformLifecycle.UPCOMING


def test_testing_broadcasts_are_skipped() -> None:
    item = {
        "id": "teststream",
        "snippet": {"channelId": CHANNEL_A, "title": "test"},
        "status": {"lifeCycleStatus": "testing"},
    }
    assert discovered_from_live_broadcast_item(item) is None


def test_collect_paginated_items_follows_next_page_token() -> None:
    pages = [
        {"items": [{"id": "a"}], "nextPageToken": "p2"},
        {"items": [{"id": "b"}, {"id": "c"}], "nextPageToken": "p3"},
        {"items": [{"id": "d"}]},
    ]

    def fetch(token):
        if token is None:
            return pages[0]
        if token == "p2":
            return pages[1]
        if token == "p3":
            return pages[2]
        raise AssertionError(token)

    items = collect_paginated_items(fetch)
    assert [item["id"] for item in items] == ["a", "b", "c", "d"]


def test_client_paginates_search_and_hydrates_videos() -> None:
    transport = QueueTransport(
        [
            {
                "items": [
                    {
                        "id": {"videoId": "page1id"},
                        "snippet": {"channelId": CHANNEL_A, "title": "one"},
                    }
                ],
                "nextPageToken": "NEXT",
            },
            {
                "items": [
                    {
                        "id": {"videoId": "page2id"},
                        "snippet": {"channelId": CHANNEL_A, "title": "two"},
                    }
                ]
            },
            {"items": []},  # upcoming
            {
                "items": [
                    {
                        "id": "page1id",
                        "snippet": {
                            "channelId": CHANNEL_A,
                            "title": "one",
                            "liveBroadcastContent": "upcoming",
                        },
                        "liveStreamingDetails": {
                            "scheduledStartTime": "2026-09-22T00:00:00Z"
                        },
                    },
                    {
                        "id": "page2id",
                        "snippet": {
                            "channelId": CHANNEL_A,
                            "title": "two",
                            "liveBroadcastContent": "live",
                        },
                        "liveStreamingDetails": {
                            "actualStartTime": "2026-09-21T18:00:00Z"
                        },
                    },
                ]
            },
        ]
    )
    client = YouTubeDataApiClient(api_key="test-key", transport=transport)
    found = client.list_channel_broadcasts(CHANNEL_A)
    ids = {item.broadcast_id: item.lifecycle for item in found}
    assert ids == {
        "page1id": PlatformLifecycle.UPCOMING,
        "page2id": PlatformLifecycle.LIVE,
    }
    paths = [call[0] for call in transport.calls]
    assert paths.count("/search") == 3  # live page1, live page2, upcoming
    assert paths[-1] == "/videos"
    assert transport.calls[0][1]["pageToken"] if False else "pageToken" not in transport.calls[0][1]
    assert transport.calls[1][1]["pageToken"] == "NEXT"
    assert "test-key" in transport.calls[0][1]["key"]


def test_client_drops_wrong_channel_video() -> None:
    transport = QueueTransport(
        [
            {
                "items": [
                    {
                        "id": {"videoId": "other"},
                        "snippet": {"channelId": CHANNEL_B},
                    }
                ]
            },
            {"items": []},
            {
                "items": [
                    {
                        "id": "other",
                        "snippet": {
                            "channelId": CHANNEL_B,
                            "liveBroadcastContent": "live",
                        },
                        "liveStreamingDetails": {
                            "actualStartTime": "2026-09-21T18:00:00Z"
                        },
                    }
                ]
            },
        ]
    )
    client = YouTubeDataApiClient(api_key="k", transport=transport)
    assert client.list_channel_broadcasts(CHANNEL_A) == ()


def test_quota_error_does_not_include_api_key() -> None:
    transport = QueueTransport(
        [
            YouTubeApiError(
                "YouTube API HTTP 403: quotaExceeded key=SUPERSECRET",
                status=403,
                reason="quotaExceeded",
            )
        ]
    )
    client = YouTubeDataApiClient(api_key="SUPERSECRET", transport=transport)
    try:
        client.list_channel_broadcasts(CHANNEL_A)
        raise AssertionError("expected YouTubeApiError")
    except YouTubeApiError as exc:
        text = sanitize_control_plane_text(str(exc)) or ""
        assert "SUPERSECRET" not in text
        assert "quotaExceeded" in str(exc) or "403" in str(exc)


def test_sanitize_redacts_keys_tokens_and_media_urls() -> None:
    raw = (
        "https://www.googleapis.com/youtube/v3/search?key=abc123 "
        "Authorization: Bearer ya29.token "
        "https://rr1.googlevideo.com/videoplayback?expire=1&signature=deadbeef"
    )
    cleaned = sanitize_control_plane_text(raw) or ""
    assert "abc123" not in cleaned
    assert "ya29.token" not in cleaned
    assert "googlevideo" not in cleaned
    assert "<redacted>" in cleaned or "<redacted-media-url>" in cleaned


def test_filesystem_lookup_binds_exact_channel(tmp_path) -> None:
    import shutil
    from pathlib import Path

    from lsgc.broadcast.calibration import FilesystemCalibrationLookup

    channel = "UCz3q0f-dMPNIyQhntPTE7gQ"
    dest = tmp_path / channel
    dest.mkdir()
    root = Path("tests/fixtures")
    shutil.copy(root / "m1_fixture_profile.json", dest / "profile.json")
    shutil.copy(root / "m3_reference.json", dest / "reference.json")
    shutil.copy(root / "m6_digit_glyphs.json", dest / "glyphs.json")
    lookup = FilesystemCalibrationLookup(tmp_path)
    found = lookup.get(channel)
    assert found is not None
    assert found.profile.channel_id == channel
    assert lookup.get("UC_OTHER_CHANNEL") is None
