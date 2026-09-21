"""PostgreSQL integration tests. Skipped unless LSGC_TEST_DATABASE_URL is set."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from lsgc.persist import (
    ConflictingConfirmationError,
    DurableScoreTracker,
    PersistOutcome,
    PLATFORM_YOUTUBE,
    PostgresGameRepository,
    StaleConfirmationError,
    TrackingStatus,
    apply_migrations,
    connect,
)
from lsgc.persist.config import postgres_test_dsn
from lsgc.persist.migrate import applied_versions
from lsgc.track import ApplyOutcome, ConfirmationPolicy, ConfirmedScoreChange
from tests.test_track_validator import obs

pytestmark = pytest.mark.postgres

POLICY = ConfirmationPolicy()


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


def _init_change(left: int, right: int, revision: int = 1) -> ConfirmedScoreChange:
    return ConfirmedScoreChange(
        revision=revision,
        previous_left=None if revision == 1 else 0,
        previous_right=None if revision == 1 else 0,
        new_left=left,
        new_right=right,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=revision == 1,
    )


class TestMigrations:
    def test_empty_database_migrates_reproducibly(self, postgres_dsn: str) -> None:
        with connect(postgres_dsn) as conn:
            try:
                conn.execute(
                    "DROP TABLE IF EXISTS confirmed_matchup_changes, confirmed_score_changes, games, schema_migrations CASCADE"
                )
                conn.commit()
                first = apply_migrations(conn)
                conn.commit()
                assert 1 in first
                assert 2 in first
                assert 3 in first
                again = apply_migrations(conn)
                conn.commit()
                assert again == []
                assert applied_versions(conn) == [1, 2, 3]
                tables = conn.execute(
                    """
                    SELECT tablename FROM pg_tables
                     WHERE schemaname = 'public'
                     ORDER BY tablename
                    """
                ).fetchall()
                names = {row["tablename"] for row in tables}
                assert "games" in names
                assert "confirmed_score_changes" in names
                assert "confirmed_matchup_changes" in names
                assert "schema_migrations" in names
            finally:
                apply_migrations(conn)
                conn.commit()


class TestConstraints:
    def test_uninitialized_game_has_null_scores(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="hBblAZD-ApY"
        )
        assert game.pair is None
        assert game.score_revision == 0
        repo.assert_current_matches_history(game.id)
        same = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="hBblAZD-ApY"
        )
        assert same.id == game.id

    def test_revision_zero_cannot_store_zero_zero(self, repo: PostgresGameRepository) -> None:
        from psycopg.errors import CheckViolation

        with pytest.raises(CheckViolation):
            with repo._conn.transaction():
                repo._conn.execute(
                    """
                    INSERT INTO games (
                        id, platform, external_broadcast_id, tracking_status,
                        trusted_left_score, trusted_right_score, score_revision,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), 'youtube', 'bad-zero', 'TRACKING',
                        0, 0, 0, NOW(), NOW()
                    )
                    """
                )

    def test_duplicate_broadcast_identity_rejected(self, repo: PostgresGameRepository) -> None:
        repo.create_or_get_game(platform=PLATFORM_YOUTUBE, external_broadcast_id="abc")
        from psycopg.errors import UniqueViolation

        with pytest.raises(UniqueViolation):
            with repo._conn.transaction():
                repo._conn.execute(
                    """
                    INSERT INTO games (
                        id, platform, external_broadcast_id, tracking_status,
                        score_revision, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), 'youtube', 'abc', 'TRACKING',
                        0, NOW(), NOW()
                    )
                    """
                )


class TestTransactionalConfirmation:
    def test_append_and_update_are_atomic(self, repo: PostgresGameRepository) -> None:
        tracker = DurableScoreTracker.open(
            repo, external_broadcast_id="atomic1", policy=POLICY
        )
        tracker.apply(obs(0, 0, index=0, ts=0.0))
        tracker.apply(obs(0, 0, index=1, ts=2.0))
        tracker.apply(obs(7, 0, index=2, ts=4.0))
        tracker.apply(obs(7, 0, index=3, ts=6.0))
        repo.assert_current_matches_history(tracker.game_id)
        assert tracker.game.pair == (7, 0)
        history = repo.list_confirmed_changes(tracker.game_id)
        assert [c.revision for c in history] == [1, 2]

    def test_injected_failure_after_event_insert_rolls_back(
        self, repo: PostgresGameRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="rollback1"
        )
        real = repo._insert_and_update

        def partial(game_id, change):
            real(game_id, change)
            raise RuntimeError("injected after both writes, still uncommitted")

        monkeypatch.setattr(repo, "_insert_and_update", partial)
        change = _init_change(0, 0)
        with pytest.raises(RuntimeError, match="injected"):
            repo.persist_confirmation(game.id, change)
        repo.assert_current_matches_history(game.id)
        assert repo.get_game(game.id).score_revision == 0
        assert repo.list_confirmed_changes(game.id) == ()

        monkeypatch.setattr(repo, "_insert_and_update", real)
        applied = repo.persist_confirmation(game.id, change)
        assert applied.outcome is PersistOutcome.APPLIED
        repo.assert_current_matches_history(game.id)

    def test_idempotent_stale_and_conflict(self, repo: PostgresGameRepository) -> None:
        tracker = DurableScoreTracker.open(
            repo, external_broadcast_id="idemp1", policy=POLICY
        )
        tracker.apply(obs(14, 7, index=0, ts=0.0))
        result = tracker.apply(obs(14, 7, index=1, ts=2.0))
        change = result.change
        assert change is not None
        again = repo.persist_confirmation(tracker.game_id, change)
        assert again.outcome is PersistOutcome.IDEMPOTENT
        tracker.apply(obs(21, 7, index=2, ts=4.0))
        tracker.apply(obs(21, 7, index=3, ts=6.0))
        with pytest.raises(StaleConfirmationError):
            repo.persist_confirmation(tracker.game_id, change)
        conflict = replace(change, new_left=99, new_right=1)
        # revision 1 vs current 2 is stale, not conflict — bump to current revision
        current = repo.list_confirmed_changes(tracker.game_id)[-1]
        conflict = replace(current, new_left=99, new_right=1)
        with pytest.raises(ConflictingConfirmationError, match="different data"):
            repo.persist_confirmation(tracker.game_id, conflict)
        assert tracker.game.pair == (21, 7)

    def test_concurrent_first_confirmation(self, migrated_dsn: str) -> None:
        with connect(migrated_dsn) as setup:
            with setup.transaction():
                setup.execute("TRUNCATE confirmed_score_changes, games CASCADE")
            repo = PostgresGameRepository(setup)
            game = repo.create_or_get_game(
                platform=PLATFORM_YOUTUBE, external_broadcast_id="race1"
            )
            game_id = game.id
            setup.commit()

        results: list[object] = []

        def worker(left: int) -> None:
            try:
                with connect(migrated_dsn) as conn:
                    r = PostgresGameRepository(conn)
                    r.persist_confirmation(game_id, _init_change(left, 0))
                    conn.commit()
                results.append(("ok", left))
            except Exception as exc:
                results.append(("err", type(exc).__name__, left))

        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(7,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        oks = [item for item in results if item[0] == "ok"]
        errs = [item for item in results if item[0] == "err"]
        assert len(oks) == 1
        assert len(errs) == 1
        with connect(migrated_dsn) as conn:
            repo = PostgresGameRepository(conn)
            repo.assert_current_matches_history(game_id)
            assert repo.get_game(game_id).score_revision == 1
            assert len(repo.list_confirmed_changes(game_id)) == 1


class TestRestartPostgres:
    def test_reload_from_new_connection(self, migrated_dsn: str) -> None:
        with connect(migrated_dsn) as conn:
            with conn.transaction():
                conn.execute("TRUNCATE confirmed_score_changes, games CASCADE")
            repo = PostgresGameRepository(conn)
            tracker = DurableScoreTracker.open(
                repo, external_broadcast_id="restart1", policy=POLICY
            )
            tracker.apply(obs(10, 7, index=9, ts=18.0))
            tracker.apply(obs(10, 7, index=10, ts=20.0))
            tracker.apply(obs(17, 7, index=11, ts=22.0))
            assert tracker.validator.candidate is not None
            game_id = tracker.game_id
            conn.commit()

        with connect(migrated_dsn) as conn:
            repo = PostgresGameRepository(conn)
            restarted = DurableScoreTracker.open(
                repo, external_broadcast_id="restart1", policy=POLICY
            )
            assert restarted.game_id == game_id
            assert restarted.game.pair == (10, 7)
            assert restarted.validator.candidate is None
            restarted.apply(obs(17, 7, index=0, ts=0.0, epoch=1))
            restarted.apply(obs(17, 7, index=1, ts=2.0, epoch=1))
            assert restarted.game.pair == (17, 7)
            repo.assert_current_matches_history(restarted.game_id)
            conn.commit()

    def test_acquisition_ended_not_final(self, repo: PostgresGameRepository) -> None:
        tracker = DurableScoreTracker.open(
            repo, external_broadcast_id="ended1", policy=POLICY
        )
        tracker.apply(obs(24, 56, index=0, ts=0.0))
        tracker.apply(obs(24, 56, index=1, ts=2.0))
        tracker.end_acquisition()
        assert tracker.game.tracking_status is TrackingStatus.ACQUISITION_ENDED
        restarted = DurableScoreTracker.open(
            repo, external_broadcast_id="ended1", policy=POLICY
        )
        assert restarted.game.pair == (24, 56)
        assert restarted.apply(obs(0, 0, index=0, ts=0.0)).outcome is ApplyOutcome.FROZEN
        assert "final" not in restarted.game.tracking_status.value.lower()
        assert restarted.game.tracking_status is not TrackingStatus("TRACKING")
