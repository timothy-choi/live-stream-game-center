"""PostgreSQL integration for the M11 API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from lsgc.api.app import create_app
from lsgc.api.hub import GameEventHub
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist import PooledGameRepository, apply_migrations, connect, open_pool
from lsgc.persist.config import postgres_test_dsn
from lsgc.persist.models import PlatformLifecycle
from api_support import (
    FakeOrchestrator,
    collect_live_sse,
    make_settings,
    matchup_change,
    parse_sse_text,
    score_change,
    upsert,
)

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    dsn = postgres_test_dsn()
    if not dsn:
        pytest.skip("LSGC_TEST_DATABASE_URL is not set")
    try:
        with connect(dsn) as conn:
            conn.execute("SELECT 1")
    except Exception:
        pytest.skip("PostgreSQL test database is not reachable")
    return dsn


@pytest.fixture(scope="session")
def migrated_dsn(postgres_dsn: str) -> str:
    with connect(postgres_dsn) as conn:
        apply_migrations(conn)
        conn.commit()
    return postgres_dsn


@pytest.fixture
def pool_store(migrated_dsn: str):
    with connect(migrated_dsn) as conn:
        conn.execute(
            "TRUNCATE confirmed_matchup_changes, confirmed_score_changes, games CASCADE"
        )
        conn.commit()
    hub = GameEventHub(DEFAULT_CATALOG)
    pool = open_pool(migrated_dsn, min_size=1, max_size=6)
    store = PooledGameRepository(pool, publisher=hub)
    yield store, hub
    store.close()


def _client(store, hub):
    app = create_app(
        store=store,
        hub=hub,
        settings=make_settings(),
        catalog=DEFAULT_CATALOG,
        orchestrator=FakeOrchestrator(),
    )
    from fastapi.testclient import TestClient

    return TestClient(app)


class TestApiPostgres:
    def test_score_matchup_lifecycle_exposed(self, pool_store) -> None:
        store, hub = pool_store
        game = upsert(store, "pgLiveGame0001", PlatformLifecycle.LIVE)
        store.persist_matchup_confirmation(game.id, matchup_change("SF", "GB"))
        store.persist_confirmation(game.id, score_change(1, 14, 7))
        store.persist_confirmation(game.id, score_change(2, 21, 7, 14, 7))
        client = _client(store, hub)
        with client:
            detail = client.get(f"/api/games/{game.id}").json()
            timeline = client.get(f"/api/games/{game.id}/score-events").json()
            listed = client.get("/api/games", params={"lifecycle": "LIVE"}).json()
        assert detail["matchup"]["left_team"]["id"] == "SF"
        assert detail["matchup"]["right_team"]["id"] == "GB"
        assert detail["score"]["revision"] == 2
        assert detail["score"]["status"] == "TRUSTED"
        assert [item["revision"] for item in timeline["events"]] == [1, 2]
        ours = next(item for item in listed["games"] if item["id"] == str(game.id))
        assert ours["platform_lifecycle"] == "LIVE"

    def test_restart_does_not_change_api_state(self, pool_store, migrated_dsn) -> None:
        store, hub = pool_store
        game = upsert(store, "pgRestartGame1", PlatformLifecycle.LIVE)
        store.persist_matchup_confirmation(game.id, matchup_change())
        store.persist_confirmation(game.id, score_change(1, 3, 0))
        game_id = game.id
        store.close()
        hub2 = GameEventHub(DEFAULT_CATALOG)
        store2 = PooledGameRepository(open_pool(migrated_dsn), publisher=hub2)
        client = _client(store2, hub2)
        with client:
            body = client.get(f"/api/games/{game_id}").json()
        assert body["score"]["revision"] == 1
        assert body["matchup"]["status"] == "CONFIRMED"
        store2.close()

    def test_realtime_catchup_uses_durable_state(self, pool_store) -> None:
        store, hub = pool_store
        game = upsert(store, "pgCatchupGame1", PlatformLifecycle.LIVE)
        client = _client(store, hub)
        with client:
            client.get(f"/api/games/{game.id}/events", params={"once": True})
            store.persist_confirmation(game.id, score_change(1, 6, 0))
            store.persist_confirmation(game.id, score_change(2, 13, 0, 6, 0))
            response = client.get(f"/api/games/{game.id}/events", params={"once": True})
            timeline = client.get(f"/api/games/{game.id}/score-events").json()
        events, _ = parse_sse_text(response.text)
        assert events[0][1]["score_revision"] == 2
        assert timeline["total"] == 2

    def test_concurrent_reads(self, pool_store) -> None:
        store, hub = pool_store
        game = upsert(store, "pgConcurrent01", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 7, 7))
        client = _client(store, hub)

        def read() -> int:
            return client.get(f"/api/games/{game.id}").json()["score"]["revision"]

        with client:
            with ThreadPoolExecutor(max_workers=6) as pool:
                revisions = list(pool.map(lambda _: read(), range(12)))
        assert revisions == [1] * 12

    def test_metadata_sync_and_api_read_coexist(self, pool_store) -> None:
        store, hub = pool_store
        game = upsert(store, "pgSyncRead0001", PlatformLifecycle.UPCOMING)
        client = _client(store, hub)
        errors: list[str] = []

        def read_once() -> None:
            try:
                body = client.get(f"/api/games/{game.id}").json()
                assert body["id"] == str(game.id)
            except Exception as exc:  # pragma: no cover
                errors.append(str(exc))

        def writer() -> None:
            try:
                store.upsert_broadcast_metadata(
                    platform=game.platform,
                    external_broadcast_id=game.external_broadcast_id,
                    channel_id=game.channel_id,
                    title="synced title",
                    platform_lifecycle=PlatformLifecycle.LIVE,
                    scheduled_start_at=game.scheduled_start_at,
                    actual_start_at=datetime.now(tz=UTC),
                    actual_end_at=None,
                    synced_at=datetime.now(tz=UTC),
                )
            except Exception as exc:  # pragma: no cover
                errors.append(str(exc))

        with client:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(read_once) for _ in range(8)]
                futures.append(pool.submit(writer))
                for future in futures:
                    future.result(timeout=5)
            body = client.get(f"/api/games/{game.id}").json()
        assert errors == []
        assert body["platform_lifecycle"] == "LIVE"

    def test_score_persist_and_subscription_coexist(self, pool_store) -> None:
        store, hub = pool_store
        game = upsert(store, "pgSubWrite0001", PlatformLifecycle.LIVE)
        client = _client(store, hub)

        with client:
            events, _ = collect_live_sse(
                client.app,
                game.id,
                min_events=2,
                mutate=lambda: store.persist_confirmation(game.id, score_change(1, 10, 0)),
            )
        assert any(item[1]["score_revision"] >= 1 for item in events)
        assert store.get_game(game.id).score_revision == 1
