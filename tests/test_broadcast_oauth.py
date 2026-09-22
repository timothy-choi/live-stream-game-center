"""OAuth-backed M9 discovery. Offline. No real Google access."""

from __future__ import annotations

import threading
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from lsgc.api.app import create_app
from lsgc.api.config import ApiSettings
from lsgc.broadcast.calibration import MappingCalibrationLookup
from lsgc.broadcast.models import ConnectedChannel, PlatformLifecycle
from lsgc.broadcast.orchestrator import BroadcastOrchestrator
from lsgc.broadcast.supervisor import TrackingSupervisor
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.broadcast.youtube import YouTubeDataApiClient
from lsgc.youtube_oauth.catalog import HybridBroadcastCatalog, merge_sync_channels
from persist_memory import InMemoryGameRepository
from tests.broadcast_fakes import (
    CHANNEL_A,
    CHANNEL_B,
    DummyCalibration,
    FakeClock,
    QueueTransport,
    RecordingSessionFactory,
    absence_pipeline_factory,
    discovered,
)
from tests.oauth_support import ScriptedYoutubeClient, make_service


def _connect(store, channel_id=CHANNEL_A, title="Channel A"):
    service = make_service(store, youtube=ScriptedYoutubeClient(channel_id, title))
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    service.complete_authorization(code="code", state=state, error=None)
    return service


def test_authorized_channel_uses_mine_true() -> None:
    transport = QueueTransport(
        [
            {
                "items": [
                    {
                        "id": "UCz3q0f-dMPNIyQhntPTE7gQ",
                        "snippet": {"title": "Arcade"},
                    }
                ]
            }
        ]
    )
    client = YouTubeDataApiClient(access_token="oauth-access", transport=transport)
    channel_id, title = client.list_authorized_channel()
    assert channel_id == "UCz3q0f-dMPNIyQhntPTE7gQ"
    assert title == "Arcade"
    path, params, headers = transport.calls[0]
    assert path == "/channels"
    assert params["mine"] == "true"
    assert headers["Authorization"] == "Bearer oauth-access"
    assert "key" not in params


def test_live_broadcasts_list_uses_mine_and_filters_channel() -> None:
    transport = QueueTransport(
        [
            {
                "items": [
                    {
                        "id": "OWN123",
                        "snippet": {
                            "channelId": CHANNEL_A,
                            "title": "mine",
                            "scheduledStartTime": "2026-09-22T00:00:00Z",
                        },
                        "status": {"lifeCycleStatus": "ready"},
                    },
                    {
                        "id": "OTHER456",
                        "snippet": {
                            "channelId": CHANNEL_B,
                            "title": "other",
                        },
                        "status": {"lifeCycleStatus": "live"},
                    },
                ]
            },
        ]
    )
    client = YouTubeDataApiClient(access_token="oauth-access", transport=transport)
    found = client.list_channel_broadcasts(CHANNEL_A)
    assert [item.broadcast_id for item in found] == ["OWN123"]
    assert found[0].lifecycle is PlatformLifecycle.UPCOMING
    assert [call[0] for call in transport.calls] == ["/liveBroadcasts"]
    assert transport.calls[0][1]["mine"] == "true"
    assert "broadcastStatus" not in transport.calls[0][1]


def test_hybrid_catalog_uses_stored_oauth_not_developer_key() -> None:
    store = InMemoryGameRepository()
    service = _connect(store)
    items = [
        discovered("ABC123", lifecycle=PlatformLifecycle.UPCOMING),
        discovered("XYZ999", channel_id=CHANNEL_B, lifecycle=PlatformLifecycle.LIVE),
    ]

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def list_channel_broadcasts(self, channel_id: str):
            return tuple(item for item in items if item.channel_id == channel_id)

        def refresh_videos(self, video_ids):
            return ()

    catalog = HybridBroadcastCatalog(
        store=store,
        service=service,
        developer_api_key="dev-key",
        client_factory=Client,
    )
    found = catalog.list_channel_broadcasts(CHANNEL_A)
    assert [item.broadcast_id for item in found] == ["ABC123"]


def test_merge_sync_channels_oauth_wins() -> None:
    store = InMemoryGameRepository()
    _connect(store, CHANNEL_A, "OAuth A")
    merged = merge_sync_channels(
        store,
        (ConnectedChannel(channel_id=CHANNEL_A, display_name="Dev A"),),
    )
    assert [item.channel_id for item in merged] == [CHANNEL_A]
    assert merged[0].display_name == "OAuth A"


def test_oauth_discovery_upcoming_live_and_worker() -> None:
    store = InMemoryGameRepository()
    service = _connect(store)
    current = [
        discovered(
            "ABC123",
            lifecycle=PlatformLifecycle.UPCOMING,
            title="upcoming",
        )
    ]

    class Client:
        def __init__(self, **kwargs):
            pass

        def list_channel_broadcasts(self, channel_id: str):
            return tuple(item for item in current if item.channel_id == channel_id)

        def refresh_videos(self, video_ids):
            return ()

    catalog = HybridBroadcastCatalog(
        store=store, service=service, client_factory=Client
    )
    lookup = MappingCalibrationLookup({CHANNEL_A: DummyCalibration(CHANNEL_A)})
    clock = FakeClock()
    orch = BroadcastOrchestrator(
        (),
        ChannelSynchronizer(catalog, store, clock=clock),
        TrackingSupervisor(
            store,
            lookup,
            session_factory=RecordingSessionFactory(hold=threading.Event()),
            pipeline_factory=absence_pipeline_factory,
            clock=clock,
        ),
        store,
        lookup,
        interval_s=30.0,
        clock=clock,
        channel_provider=lambda: merge_sync_channels(store, ()),
    )
    first = orch.reconcile_once()
    game = store.get_game_by_external_id("youtube", "ABC123")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.UPCOMING
    assert first.upcoming_count == 1
    assert first.active_worker_count == 0

    current[0] = discovered(
        "ABC123",
        lifecycle=PlatformLifecycle.LIVE,
        title="live",
    )
    second = orch.reconcile_once()
    game = store.get_game_by_external_id("youtube", "ABC123")
    assert game.platform_lifecycle is PlatformLifecycle.LIVE
    assert second.live_count == 1
    assert second.active_worker_count == 1
    orch.shutdown()


def test_api_starts_with_orchestrator_and_zero_connections(monkeypatch) -> None:
    monkeypatch.delenv("LSGC_YOUTUBE_CHANNEL_ID", raising=False)
    monkeypatch.delenv("LSGC_YOUTUBE_CHANNEL_IDS", raising=False)
    monkeypatch.delenv("LSGC_YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("LSGC_YOUTUBE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LSGC_GOOGLE_CLIENT_ID", raising=False)
    store = InMemoryGameRepository()
    app = create_app(
        store=store,
        settings=ApiSettings(
            cors_origins=(),
            run_orchestrator=True,
            heartbeat_s=15,
            max_queue=8,
        ),
        connection_service=None,
        calibration=MappingCalibrationLookup(),
    )
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        diag = client.get("/api/diagnostics/orchestrator").json()
        assert diag["orchestrator_enabled"] is True
        assert diag["active_worker_count"] == 0
        listed = client.get("/api/youtube/connections").json()
        assert listed["connections"] == []
