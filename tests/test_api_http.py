"""HTTP product API tests. Offline, no YouTube, no tracking startup."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from lsgc.api.hub import GameEventHub
from lsgc.api.schemas import GameSummary, ScoreStatus
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist.models import PlatformLifecycle, TrackingStatus
from api_support import (
    CHANNEL,
    FakeOrchestrator,
    make_client,
    matchup_change,
    score_change,
    upsert,
)
from persist_memory import InMemoryGameRepository


def _app(store=None, hub=None, orchestrator=None):
    hub = hub or GameEventHub(DEFAULT_CATALOG)
    store = store or InMemoryGameRepository(publisher=hub)
    orch = orchestrator or FakeOrchestrator()
    client = make_client(store, hub, orchestrator=orch)
    return client, store, hub, orch


class TestHealth:
    def test_process_alive_and_db_ok(self) -> None:
        client, _, _, _ = _app()
        with client:
            body = client.get("/health").json()
        assert body["process"] == "alive"
        assert body["database"] == "ok"
        assert body["status"] == "ok"

    def test_database_unavailable(self) -> None:
        class Dead:
            def ping(self) -> bool:
                raise RuntimeError("db down")

        client = make_client(Dead(), GameEventHub(DEFAULT_CATALOG))
        with client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["process"] == "alive"
        assert response.json()["database"] == "unavailable"
        assert response.json()["status"] == "degraded"


class TestGameList:
    def test_list_and_lifecycle_order(self) -> None:
        client, store, _, _ = _app()
        ended = upsert(store, "endedGameId01", PlatformLifecycle.ENDED, hours=-2)
        upcoming_late = upsert(store, "upcomingLate01", PlatformLifecycle.UPCOMING, hours=5)
        upcoming_soon = upsert(store, "upcomingSoon01", PlatformLifecycle.UPCOMING, hours=1)
        live = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            body = client.get("/api/games").json()
        ids = [item["id"] for item in body["games"]]
        assert ids[:4] == [str(live.id), str(upcoming_soon.id), str(upcoming_late.id), str(ended.id)]
        assert body["total"] == 4

    def test_lifecycle_filter_and_channel(self) -> None:
        client, store, _, _ = _app()
        upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        upsert(store, "upcomingSoon01", PlatformLifecycle.UPCOMING)
        upsert(store, "otherChanLive1", PlatformLifecycle.LIVE, channel_id="UCotherChannel000000000000")
        with client:
            live = client.get("/api/games", params={"lifecycle": "LIVE"}).json()
            upcoming = client.get("/api/games", params={"lifecycle": "UPCOMING"}).json()
            channel = client.get("/api/games", params={"channel_id": CHANNEL}).json()
            bad = client.get("/api/games", params={"lifecycle": "FINAL"})
        assert live["total"] == 2
        assert all(item["platform_lifecycle"] == "LIVE" for item in live["games"])
        assert upcoming["total"] == 1
        assert channel["total"] == 2
        assert bad.status_code == 400

    def test_pagination(self) -> None:
        client, store, _, _ = _app()
        for index in range(3):
            upsert(store, f"liveGameId000{index}", PlatformLifecycle.LIVE)
        with client:
            page = client.get("/api/games", params={"limit": 2, "offset": 0}).json()
            rest = client.get("/api/games", params={"limit": 2, "offset": 2}).json()
            invalid = client.get("/api/games", params={"limit": 0})
        assert page["limit"] == 2
        assert len(page["games"]) == 2
        assert rest["total"] == 3
        assert len(rest["games"]) == 1
        assert invalid.status_code == 422


class TestGameDetail:
    def test_unknown_game_404(self) -> None:
        client, _, _, _ = _app()
        with client:
            response = client.get(f"/api/games/{uuid4()}")
        assert response.status_code == 404
        assert response.json() == {"detail": "game not found"}

    def test_upcoming_null_score_and_matchup(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "upcomingSoon01", PlatformLifecycle.UPCOMING)
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["platform_lifecycle"] == "UPCOMING"
        assert body["score"] == {
            "status": "UNCONFIRMED",
            "left": None,
            "right": None,
            "revision": 0,
        }
        assert body["matchup"]["status"] == "UNCONFIRMED"
        assert body["matchup"]["left_team"] is None
        assert body["matchup"]["right_team"] is None
        assert body["platform"] == "youtube"
        assert body["external_broadcast_id"] == "upcomingSoon01"

    def test_live_trusted_score_and_teams(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        store.persist_matchup_confirmation(game.id, matchup_change())
        store.persist_confirmation(game.id, score_change(1, 7, 3))
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["platform_lifecycle"] == "LIVE"
        assert body["score"]["status"] == "TRUSTED"
        assert body["score"]["left"] == 7
        assert body["score"]["right"] == 3
        assert body["score"]["revision"] == 1
        assert body["matchup"]["status"] == "CONFIRMED"
        assert body["matchup"]["left_team"]["id"] == "SF"
        assert body["matchup"]["left_team"]["display_name"] == "San Francisco 49ers"
        assert body["matchup"]["right_team"]["abbreviation"] == "NYG"
        assert "FINAL" not in body["score"]["status"]

    def test_ended_remains_trusted(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "endedGameId01", PlatformLifecycle.ENDED)
        store.persist_matchup_confirmation(game.id, matchup_change())
        store.persist_confirmation(game.id, score_change(1, 21, 17))
        store.mark_acquisition_ended(game.id)
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["platform_lifecycle"] == "ENDED"
        assert body["tracking_status"] == TrackingStatus.ACQUISITION_ENDED.value
        assert body["score"]["status"] == ScoreStatus.TRUSTED.value
        assert body["score"]["left"] == 21
        assert "FINAL" not in str(body)

    def test_matchup_conflict_preserves_teams(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "conflictGame01", PlatformLifecycle.LIVE)
        store.persist_matchup_confirmation(game.id, matchup_change("SF", "NYG"))
        store.record_matchup_conflict(game.id, ("LAC", "GB"))
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["matchup"]["status"] == "CONFLICT"
        assert body["matchup"]["left_team"]["id"] == "SF"
        assert body["matchup"]["right_team"]["id"] == "NYG"
        assert body["matchup"]["conflict_pair"] == {
            "left_team_id": "LAC",
            "right_team_id": "GB",
        }

    def test_tracking_disabled(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "disabledGame01", PlatformLifecycle.LIVE)
        store.set_tracking_enabled(game.id, False)
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["tracking_enabled"] is False

    def test_iso_utc_timestamps(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "upcomingSoon01", PlatformLifecycle.UPCOMING)
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        for key in ("scheduled_start_at", "last_updated_at"):
            value = body[key]
            assert value.endswith("Z")
            assert "T" in value
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            assert parsed.tzinfo is not None
            assert parsed.utcoffset().total_seconds() == 0

    def test_no_secret_or_internal_fields(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 3, 0))
        with client:
            raw = client.get(f"/api/games/{game.id}").text.lower()
            listed = client.get("/api/games").text.lower()
        forbidden = (
            "password",
            "database_url",
            "api_key",
            "access_token",
            "cookie",
            "signed",
            "postgresql://",
            "evidence_count",
            "traceback",
            "psycopg",
        )
        for token in forbidden:
            assert token not in raw
            assert token not in listed

    def test_reads_do_not_start_tracking(self) -> None:
        orch = FakeOrchestrator()
        client, store, _, _ = _app(orchestrator=orch)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            client.get(f"/api/games/{game.id}")
            client.get("/api/games")
            client.get(f"/api/games/{game.id}/score-events")
        assert orch.ensure_calls == []


class TestScoreTimeline:
    def test_ordered_revisions(self) -> None:
        client, store, _, _ = _app()
        game = upsert(store, "timelineGame1", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 0, 0))
        store.persist_confirmation(game.id, score_change(2, 7, 0, 0, 0))
        store.persist_confirmation(game.id, score_change(3, 7, 3, 7, 0))
        with client:
            body = client.get(f"/api/games/{game.id}/score-events").json()
        revisions = [item["revision"] for item in body["events"]]
        assert revisions == [1, 2, 3]
        assert body["events"][0]["initialization"] is True
        assert body["events"][0]["previous_left"] is None
        assert body["events"][2]["left"] == 7
        assert body["events"][2]["right"] == 3
        assert body["events"][0]["confirmed_at"].endswith("Z")

    def test_unknown_timeline_404(self) -> None:
        client, _, _, _ = _app()
        with client:
            assert client.get(f"/api/games/{uuid4()}/score-events").status_code == 404


class TestOpenAPIAndReadonly:
    def test_openapi_includes_game_summary(self) -> None:
        client, _, _, _ = _app()
        with client:
            spec = client.get("/openapi.json").json()
        schemas = spec["components"]["schemas"]
        assert "GameSummary" in schemas
        assert "ScoreSummary" in schemas
        assert "GameEvent" in schemas
        paths = spec["paths"]
        assert "/api/games" in paths
        assert "get" in paths["/api/games"]
        assert "post" not in paths["/api/games"]

    def test_schema_round_trip(self) -> None:
        GameSummary.model_validate(
            {
                "id": str(uuid4()),
                "platform": "youtube",
                "external_broadcast_id": "abc",
                "channel_id": CHANNEL,
                "title": "t",
                "platform_lifecycle": "UPCOMING",
                "tracking_status": "TRACKING",
                "tracking_enabled": True,
                "scheduled_start_at": "2026-09-21T20:00:00Z",
                "actual_start_at": None,
                "actual_end_at": None,
                "matchup": {
                    "status": "UNCONFIRMED",
                    "left_team": None,
                    "right_team": None,
                    "revision": 0,
                    "conflict_pair": None,
                    "conflict_count": 0,
                },
                "score": {
                    "status": "UNCONFIRMED",
                    "left": None,
                    "right": None,
                    "revision": 0,
                },
                "last_updated_at": "2026-09-21T19:00:00Z",
                "cursor": "0:0:0:UPCOMING:TRACKING:1",
            }
        )
