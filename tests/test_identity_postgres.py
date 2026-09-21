"""PostgreSQL integration for confirmed matchup persistence."""

from __future__ import annotations

import pytest

from lsgc.identity.models import ConfirmedMatchupChange, IdentityMethod, MatchupApplyOutcome
from lsgc.persist import (
    ConflictingMatchupError,
    DurableMatchupTracker,
    DurableScoreTracker,
    PersistError,
    PersistOutcome,
    PLATFORM_YOUTUBE,
    PostgresGameRepository,
    apply_migrations,
    connect,
)
from lsgc.persist.config import postgres_test_dsn
from lsgc.persist.migrate import applied_versions
from lsgc.persist.models import PlatformLifecycle
from tests.test_identity_validator import DEFAULT, obs
from tests.test_track_validator import obs as score_obs

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
def repo(migrated_dsn: str) -> PostgresGameRepository:
    conn = connect(migrated_dsn)
    with conn.transaction():
        conn.execute(
            "TRUNCATE confirmed_matchup_changes, confirmed_score_changes, games CASCADE"
        )
    yield PostgresGameRepository(conn)
    conn.close()


def _change(left: str = "SF", right: str = "GB", revision: int = 1) -> ConfirmedMatchupChange:
    return ConfirmedMatchupChange(
        revision=revision,
        left_team_id=left,
        right_team_id=right,
        left_method=IdentityMethod.LOGO,
        right_method=IdentityMethod.LOGO,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=revision == 1,
    )


class TestMatchupMigration:
    def test_migration_adds_matchup_objects(self, migrated_dsn: str) -> None:
        with connect(migrated_dsn) as conn:
            versions = applied_versions(conn)
            assert 3 in versions
            tables = {
                row["tablename"]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ).fetchall()
            }
            assert "confirmed_matchup_changes" in tables
            columns = {
                row["column_name"]
                for row in conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                     WHERE table_name = 'games'
                    """
                ).fetchall()
            }
            for required in (
                "confirmed_left_team_id",
                "confirmed_right_team_id",
                "matchup_revision",
                "matchup_confirmed_at",
            ):
                assert required in columns


class TestMatchupConstraints:
    def test_unconfirmed_game_has_null_matchup(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="idNull"
        )
        assert game.matchup_revision == 0
        assert game.confirmed_left_team_id is None
        assert game.confirmed_right_team_id is None
        assert repo.list_matchup_changes(game.id) == ()

    def test_valid_confirmation_persists_both_teams(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="idOk"
        )
        result = repo.persist_matchup_confirmation(game.id, _change())
        assert result.outcome is PersistOutcome.APPLIED
        game = repo.get_game(game.id)
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "GB"
        assert len(repo.list_matchup_changes(game.id)) == 1

    def test_left_equals_right_rejected(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="idSame"
        )
        with pytest.raises(ConflictingMatchupError):
            repo.persist_matchup_confirmation(game.id, _change("SF", "SF"))

    def test_idempotent_retry(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="idIdem"
        )
        change = _change()
        repo.persist_matchup_confirmation(game.id, change)
        again = repo.persist_matchup_confirmation(game.id, change)
        assert again.outcome is PersistOutcome.IDEMPOTENT
        assert len(repo.list_matchup_changes(game.id)) == 1

    def test_conflicting_confirmation_rejected(self, repo: PostgresGameRepository) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="idConflict"
        )
        repo.persist_matchup_confirmation(game.id, _change("SF", "GB"))
        with pytest.raises(ConflictingMatchupError):
            repo.persist_matchup_confirmation(game.id, _change("MIN", "SF", revision=2))
        game = repo.get_game(game.id)
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "GB"

    def test_metadata_sync_does_not_overwrite_matchup(
        self, repo: PostgresGameRepository
    ) -> None:
        game = repo.create_or_get_game(
            platform=PLATFORM_YOUTUBE,
            external_broadcast_id="idMeta",
            channel_id="UC_x",
        )
        repo.persist_matchup_confirmation(game.id, _change())
        from datetime import UTC, datetime

        repo.upsert_broadcast_metadata(
            platform=PLATFORM_YOUTUBE,
            external_broadcast_id="idMeta",
            channel_id="UC_x",
            title="Chargers @ 49ers",
            platform_lifecycle=PlatformLifecycle.LIVE,
            scheduled_start_at=None,
            actual_start_at=None,
            actual_end_at=None,
            synced_at=datetime.now(tz=UTC),
        )
        game = repo.get_game_by_external_id(PLATFORM_YOUTUBE, "idMeta")
        assert game is not None
        assert game.title == "Chargers @ 49ers"
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "GB"


class TestMatchupTrackerPostgres:
    def test_restart_reload(self, repo: PostgresGameRepository) -> None:
        tracker = DurableMatchupTracker.open(
            repo, external_broadcast_id="idRestart", policy=DEFAULT
        )
        tracker.apply(obs("LV", "SF", index=0, ts=0.0))
        tracker.apply(obs("LV", "SF", index=1, ts=2.0))
        restarted = DurableMatchupTracker.open(
            repo, external_broadcast_id="idRestart", policy=DEFAULT
        )
        assert restarted.validator.state.pair == ("LV", "SF")
        assert restarted.validator.candidate is None
        assert restarted.validator._last_index is None

    def test_candidate_not_persisted(self, repo: PostgresGameRepository) -> None:
        tracker = DurableMatchupTracker.open(
            repo, external_broadcast_id="idCand", policy=DEFAULT
        )
        tracker.apply(obs("SF", "GB", index=0, ts=0.0))
        game = repo.get_game(tracker.game_id)
        assert game.matchup_revision == 0
        assert repo.list_matchup_changes(game.id) == ()

    def test_write_rollback(self, repo: PostgresGameRepository) -> None:
        tracker = DurableMatchupTracker.open(
            repo, external_broadcast_id="idFail", policy=DEFAULT
        )
        tracker.apply(obs("MIN", "SF", index=0, ts=0.0))

        def boom(_game_id, _change):
            raise PersistError("injected matchup write failure")

        repo.persist_matchup_confirmation = boom  # type: ignore[method-assign]
        with pytest.raises(PersistError, match="injected matchup"):
            tracker.apply(obs("MIN", "SF", index=1, ts=2.0))
        assert not tracker.validator.state.initialized
        assert repo.get_game(tracker.game_id).matchup_revision == 0

    def test_score_history_unchanged(self, repo: PostgresGameRepository) -> None:
        scores = DurableScoreTracker.open(repo, external_broadcast_id="idBoth")
        scores.apply(score_obs(14, 7, index=0, ts=0.0))
        scores.apply(score_obs(14, 7, index=1, ts=2.0))
        matchup = DurableMatchupTracker.open(repo, external_broadcast_id="idBoth")
        matchup.apply(obs("SF", "NYG", index=0, ts=0.0))
        matchup.apply(obs("SF", "NYG", index=1, ts=2.0))
        game = repo.get_game(scores.game_id)
        assert game.pair == (14, 7)
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "NYG"
        assert len(repo.list_confirmed_changes(game.id)) == 1
        assert len(repo.list_matchup_changes(game.id)) == 1
