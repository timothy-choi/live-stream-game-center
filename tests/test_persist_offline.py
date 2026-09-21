"""Offline persistence tests. No PostgreSQL, no network."""

from __future__ import annotations

import pytest

from lsgc.persist import (
    ConflictingConfirmationError,
    DurableScoreTracker,
    PersistError,
    PersistOutcome,
    PLATFORM_YOUTUBE,
    StaleConfirmationError,
    TrackingStatus,
)
from lsgc.persist.config import postgres_test_dsn
from lsgc.persist.repository import validate_broadcast_id
from lsgc.track import ApplyOutcome, ConfirmationPolicy
from tests.persist_memory import InMemoryGameRepository
from tests.test_track_validator import DEFAULT, feed, obs

SPARSE = ConfirmationPolicy(required_observations=2, min_span_s=2.0, max_candidate_gap_s=None)


class TestConfig:
    def test_postgres_test_dsn_ignores_non_test_database_url(self) -> None:
        assert postgres_test_dsn(environ={}) is None
        assert (
            postgres_test_dsn(environ={"DATABASE_URL": "postgresql://lsgc:x@127.0.0.1/lsgc"})
            is None
        )
        url = "postgresql://lsgc:x@127.0.0.1/lsgc_test"
        assert postgres_test_dsn(environ={"DATABASE_URL": url}) == url


class TestIdentity:
    def test_rejects_watch_url_as_identity(self) -> None:
        with pytest.raises(PersistError, match="not a URL"):
            validate_broadcast_id("youtube", "https://www.youtube.com/watch?v=hBblAZD-ApY")

    def test_create_does_not_invent_zero_zero(self) -> None:
        store = InMemoryGameRepository()
        game = store.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="hBblAZD-ApY"
        )
        assert game.score_revision == 0
        assert game.pair is None
        assert game.trusted_left_score is None
        store.assert_current_matches_history(game.id)
        again = store.create_or_get_game(
            platform=PLATFORM_YOUTUBE, external_broadcast_id="hBblAZD-ApY"
        )
        assert again.id == game.id


class TestTrackerPersistence:
    def test_initialization_and_change_persist(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidA", policy=DEFAULT
        )
        tracker.apply(obs(0, 0, index=0, ts=0.0))
        confirmed = tracker.apply(obs(0, 0, index=1, ts=2.0))
        assert confirmed.outcome is ApplyOutcome.CONFIRMED
        game = tracker.game
        assert game.pair == (0, 0)
        assert game.score_revision == 1
        tracker.apply(obs(7, 0, index=2, ts=4.0))
        tracker.apply(obs(7, 0, index=3, ts=6.0))
        assert tracker.game.pair == (7, 0)
        assert tracker.game.score_revision == 2
        history = store.list_confirmed_changes(tracker.game_id)
        assert [c.new_pair for c in history] == [(0, 0), (7, 0)]
        store.assert_current_matches_history(tracker.game_id)

    def test_idempotent_retry_same_revision(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidB", policy=DEFAULT
        )
        tracker.apply(obs(14, 7, index=0, ts=0.0))
        result = tracker.apply(obs(14, 7, index=1, ts=2.0))
        change = result.change
        assert change is not None
        second = store.persist_confirmation(tracker.game_id, change)
        assert second.outcome is PersistOutcome.IDEMPOTENT
        assert tracker.game.score_revision == 1
        assert len(store.list_confirmed_changes(tracker.game_id)) == 1

    def test_stale_revision_does_not_regress(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidC", policy=DEFAULT
        )
        tracker.apply(obs(0, 0, index=0, ts=0.0))
        tracker.apply(obs(0, 0, index=1, ts=2.0))
        first = store.list_confirmed_changes(tracker.game_id)[0]
        tracker.apply(obs(7, 0, index=2, ts=4.0))
        tracker.apply(obs(7, 0, index=3, ts=6.0))
        with pytest.raises(StaleConfirmationError):
            store.persist_confirmation(tracker.game_id, first)
        assert tracker.game.pair == (7, 0)

    def test_conflicting_same_revision_rejected(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidD", policy=DEFAULT
        )
        tracker.apply(obs(3, 7, index=0, ts=0.0))
        tracker.apply(obs(3, 7, index=1, ts=2.0))
        stored = store.list_confirmed_changes(tracker.game_id)[0]
        from dataclasses import replace

        conflict = replace(stored, new_left=99, new_right=99)
        with pytest.raises(ConflictingConfirmationError, match="different data"):
            store.persist_confirmation(tracker.game_id, conflict)
        assert tracker.game.pair == (3, 7)

    def test_write_failure_reloads_memory_from_store(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidE", policy=DEFAULT
        )
        tracker.apply(obs(14, 7, index=0, ts=0.0))
        tracker.apply(obs(14, 7, index=1, ts=2.0))
        tracker.apply(obs(21, 7, index=2, ts=4.0))
        store.fail_next_persist = True
        with pytest.raises(PersistError, match="injected"):
            tracker.apply(obs(21, 7, index=3, ts=6.0))
        assert tracker.validator.state.pair == (14, 7)
        assert tracker.validator.candidate is None
        assert tracker.game.pair == (14, 7)
        assert tracker.game.score_revision == 1
        # confirmation must be re-established
        tracker.apply(obs(21, 7, index=4, ts=8.0))
        assert tracker.validator.state.pair == (14, 7)
        tracker.apply(obs(21, 7, index=5, ts=10.0))
        assert tracker.game.pair == (21, 7)

    def test_restart_drops_candidate_and_allows_new_indexes(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidF", policy=DEFAULT
        )
        tracker.apply(obs(10, 3, index=50, ts=100.0))
        tracker.apply(obs(10, 3, index=51, ts=102.0))
        tracker.apply(obs(17, 3, index=52, ts=104.0))
        assert tracker.validator.candidate is not None
        # process restart: new tracker, same store
        restarted = DurableScoreTracker.open(
            store, external_broadcast_id="vidF", policy=DEFAULT
        )
        assert restarted.game.pair == (10, 3)
        assert restarted.validator.candidate is None
        assert len(restarted.validator.history) == 1
        first = restarted.apply(obs(17, 3, index=0, ts=0.0, epoch=1))
        assert first.outcome is not ApplyOutcome.IGNORED_STALE
        restarted.apply(obs(17, 3, index=1, ts=2.0, epoch=1))
        assert restarted.game.pair == (17, 3)
        store.assert_current_matches_history(restarted.game_id)

    def test_acquisition_ended_survives_restart_and_is_not_final(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidG", policy=DEFAULT
        )
        tracker.apply(obs(21, 14, index=0, ts=0.0))
        tracker.apply(obs(21, 14, index=1, ts=2.0))
        ended = tracker.end_acquisition()
        assert ended.outcome is ApplyOutcome.FROZEN
        assert "FINAL" not in ended.state.summary()
        assert tracker.game.tracking_status is TrackingStatus.ACQUISITION_ENDED
        restarted = DurableScoreTracker.open(
            store, external_broadcast_id="vidG", policy=DEFAULT
        )
        assert restarted.game.pair == (21, 14)
        assert restarted.game.tracking_status is TrackingStatus.ACQUISITION_ENDED
        later = restarted.apply(obs(99, 99, index=0, ts=0.0))
        assert later.outcome is ApplyOutcome.FROZEN
        assert restarted.game.pair == (21, 14)

    def test_crash_before_init_restarts_uninitialized(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="vidH", policy=DEFAULT
        )
        tracker.apply(obs(21, 14, index=0, ts=0.0))
        restarted = DurableScoreTracker.open(
            store, external_broadcast_id="vidH", policy=DEFAULT
        )
        assert not restarted.game.score_revision
        assert restarted.game.pair is None
        assert restarted.validator.candidate is None


class TestSparseReplayMemory:
    def test_giants_labels_persist_through_restart(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableScoreTracker.open(
            store, external_broadcast_id="hBblAZD-ApY", policy=SPARSE
        )
        pairs = [(0, 0), (0, 0), (7, 7), (7, 7), (14, 14), (14, 14)]
        for i, (left, right) in enumerate(pairs):
            tracker.apply(obs(left, right, index=i, ts=float(i * 10)))
        assert tracker.game.pair == (14, 14)
        assert tracker.game.score_revision == 3
        restarted = DurableScoreTracker.open(
            store, external_broadcast_id="hBblAZD-ApY", policy=SPARSE
        )
        assert restarted.game.pair == (14, 14)
        restarted.apply(obs(21, 14, index=0, ts=0.0))
        restarted.apply(obs(21, 14, index=1, ts=10.0))
        assert restarted.game.pair == (21, 14)
        store.assert_current_matches_history(restarted.game_id)
