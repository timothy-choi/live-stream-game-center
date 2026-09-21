"""PostgreSQL integration for M9 broadcast lifecycle. Skipped without DSN."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lsgc.broadcast.models import PLATFORM_YOUTUBE, PlatformLifecycle
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.persist import PostgresGameRepository, TrackingStatus, apply_migrations, connect
from lsgc.persist.config import postgres_test_dsn
from lsgc.track.models import ConfirmedScoreChange
from tests.broadcast_fakes import CHANNEL_A, FakeCatalog, discovered

pytestmark = pytest.mark.postgres

START = datetime(2026, 9, 22, 1, 0, tzinfo=UTC)


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
def repo(migrated_dsn: str) -> PostgresGameRepository:
    conn = connect(migrated_dsn)
    with conn.transaction():
        conn.execute("TRUNCATE confirmed_score_changes, games CASCADE")
    yield PostgresGameRepository(conn)
    conn.close()


def _change() -> ConfirmedScoreChange:
    return ConfirmedScoreChange(
        revision=1,
        previous_left=None,
        previous_right=None,
        new_left=14,
        new_right=7,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=True,
    )


def test_migration_adds_lifecycle_columns(migrated_dsn: str) -> None:
    with connect(migrated_dsn) as conn:
        rows = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'games'
            """
        ).fetchall()
        names = {row["column_name"] for row in rows}
        for required in (
            "title",
            "platform_lifecycle",
            "scheduled_start_at",
            "actual_start_at",
            "actual_end_at",
            "last_synced_at",
            "tracking_enabled",
            "last_tracking_error",
            "last_worker_finished_at",
        ):
            assert required in names
        versions = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in versions] == [1, 2]


def test_scheduled_becomes_live_same_row(repo: PostgresGameRepository) -> None:
    catalog = FakeCatalog(
        [
            discovered(
                "ABC123",
                lifecycle=PlatformLifecycle.UPCOMING,
                title="upcoming",
                scheduled_start=START,
            )
        ]
    )
    sync = ChannelSynchronizer(catalog, repo)
    sync.sync_channel(CHANNEL_A)
    first = repo.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert first is not None
    assert first.platform_lifecycle is PlatformLifecycle.UPCOMING
    assert first.tracking_enabled is True
    catalog.broadcasts = [
        discovered(
            "ABC123",
            lifecycle=PlatformLifecycle.LIVE,
            title="now live",
            scheduled_start=START,
            actual_start=START,
        )
    ]
    sync.sync_channel(CHANNEL_A)
    second = repo.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert second is not None
    assert second.id == first.id
    assert second.platform_lifecycle is PlatformLifecycle.LIVE
    assert second.title == "now live"
    rows = repo.list_games_for_channel(CHANNEL_A)
    assert len(rows) == 1


def test_tracking_enabled_and_ended_survive_reconnect(
    migrated_dsn: str, repo: PostgresGameRepository
) -> None:
    catalog = FakeCatalog(
        [discovered("ABC123", lifecycle=PlatformLifecycle.LIVE, title="live")]
    )
    ChannelSynchronizer(catalog, repo).sync_channel(CHANNEL_A)
    game = repo.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    repo.persist_confirmation(game.id, _change())
    repo.set_tracking_enabled(game.id, False)
    catalog.broadcasts = [
        discovered(
            "ABC123",
            lifecycle=PlatformLifecycle.ENDED,
            title="live",
            actual_end=datetime.now(tz=UTC),
        )
    ]
    ChannelSynchronizer(catalog, repo).sync_channel(CHANNEL_A)
    original_id = game.id

    with connect(migrated_dsn) as conn:
        restored = PostgresGameRepository(conn)
        loaded = restored.get_game(original_id)
        assert loaded.platform_lifecycle is PlatformLifecycle.ENDED
        assert loaded.tracking_enabled is False
        assert loaded.pair == (14, 7)
        assert loaded.score_revision == 1
        assert loaded.tracking_status is TrackingStatus.TRACKING
        history = restored.list_confirmed_changes(original_id)
        assert len(history) == 1
        assert history[0].new_pair == (14, 7)
        assert "FINAL" not in TrackingStatus.__members__
