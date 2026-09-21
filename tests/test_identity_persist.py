"""Offline durable matchup tracker: persist, restart, write failure."""

from __future__ import annotations

import pytest

from lsgc.identity.models import MatchupApplyOutcome
from lsgc.persist import (
    ConflictingMatchupError,
    DurableMatchupTracker,
    DurableScoreTracker,
    PersistError,
    PersistOutcome,
    PLATFORM_YOUTUBE,
)
from lsgc.persist.models import PlatformLifecycle
from tests.persist_memory import InMemoryGameRepository
from tests.test_identity_validator import DEFAULT, obs
from tests.test_track_validator import obs as score_obs


class TestMatchupTracker:
    def test_create_does_not_invent_matchup(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(store, external_broadcast_id="vidM")
        game = tracker.game
        assert game.matchup_revision == 0
        assert game.confirmed_left_team_id is None
        assert store.list_matchup_changes(game.id) == ()

    def test_confirmation_persists_and_survives_restart(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store, external_broadcast_id="vidA", policy=DEFAULT
        )
        tracker.apply(obs("SF", "GB", index=0, ts=0.0))
        confirmed = tracker.apply(obs("SF", "GB", index=1, ts=2.0))
        assert confirmed.outcome is MatchupApplyOutcome.CONFIRMED
        game = tracker.game
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "GB"
        assert game.matchup_revision == 1
        history = store.list_matchup_changes(tracker.game_id)
        assert len(history) == 1

        restarted = DurableMatchupTracker.open(
            store, external_broadcast_id="vidA", policy=DEFAULT
        )
        assert restarted.validator.state.pair == ("SF", "GB")
        assert restarted.validator.candidate is None
        noise = restarted.apply(obs("SF", "MIN", index=0, ts=0.0, epoch=3))
        assert noise.outcome is MatchupApplyOutcome.CONFLICT
        assert restarted.game.confirmed_left_team_id == "SF"
        assert restarted.game.confirmed_right_team_id == "GB"
        assert restarted.game.matchup_conflict_count == 1

    def test_candidate_does_not_survive_restart(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store, external_broadcast_id="vidB", policy=DEFAULT
        )
        tracker.apply(obs("SF", "GB", index=0, ts=0.0))
        assert tracker.validator.candidate is not None
        restarted = DurableMatchupTracker.open(
            store, external_broadcast_id="vidB", policy=DEFAULT
        )
        assert restarted.validator.candidate is None
        assert not restarted.game.matchup_revision

    def test_idempotent_retry(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store, external_broadcast_id="vidC", policy=DEFAULT
        )
        tracker.apply(obs("LAC", "SF", index=0, ts=0.0))
        change = tracker.apply(obs("LAC", "SF", index=1, ts=2.0)).change
        assert change is not None
        result = store.persist_matchup_confirmation(tracker.game_id, change)
        assert result.outcome is PersistOutcome.IDEMPOTENT
        assert len(store.list_matchup_changes(tracker.game_id)) == 1

    def test_conflicting_confirmation_rejected(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store, external_broadcast_id="vidD", policy=DEFAULT
        )
        tracker.apply(obs("SF", "GB", index=0, ts=0.0))
        change = tracker.apply(obs("SF", "GB", index=1, ts=2.0)).change
        assert change is not None
        other = DurableMatchupTracker.open(
            store, external_broadcast_id="vidE", policy=DEFAULT
        )
        other.apply(obs("MIN", "SF", index=0, ts=0.0))
        other_change = other.apply(obs("MIN", "SF", index=1, ts=2.0)).change
        assert other_change is not None
        with pytest.raises(ConflictingMatchupError):
            store.persist_matchup_confirmation(tracker.game_id, other_change)

    def test_write_failure_reloads_unconfirmed(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store, external_broadcast_id="vidF", policy=DEFAULT
        )
        tracker.apply(obs("LV", "SF", index=0, ts=0.0))
        store.fail_next_matchup_persist = True
        with pytest.raises(PersistError, match="injected matchup"):
            tracker.apply(obs("LV", "SF", index=1, ts=2.0))
        assert not tracker.validator.state.initialized
        assert tracker.game.matchup_revision == 0
        tracker.apply(obs("LV", "SF", index=2, ts=4.0))
        tracker.apply(obs("LV", "SF", index=3, ts=6.0))
        assert tracker.game.confirmed_left_team_id == "LV"

    def test_metadata_sync_does_not_overwrite_matchup(self) -> None:
        store = InMemoryGameRepository()
        tracker = DurableMatchupTracker.open(
            store,
            external_broadcast_id="vidG",
            channel_id="UC_x",
            policy=DEFAULT,
        )
        tracker.apply(obs("SF", "NYG", index=0, ts=0.0))
        tracker.apply(obs("SF", "NYG", index=1, ts=2.0))
        store.upsert_broadcast_metadata(
            platform=PLATFORM_YOUTUBE,
            external_broadcast_id="vidG",
            channel_id="UC_x",
            title="Chargers @ 49ers Game 2",
            platform_lifecycle=PlatformLifecycle.LIVE,
            scheduled_start_at=None,
            actual_start_at=None,
            actual_end_at=None,
            synced_at=tracker.game.updated_at,
        )
        game = store.get_game(tracker.game_id)
        assert game.title == "Chargers @ 49ers Game 2"
        assert game.confirmed_left_team_id == "SF"
        assert game.confirmed_right_team_id == "NYG"
        assert tracker.title_disagreement is not None

    def test_score_history_independent(self) -> None:
        store = InMemoryGameRepository()
        scores = DurableScoreTracker.open(store, external_broadcast_id="vidH")
        scores.apply(score_obs(7, 0, index=0, ts=0.0))
        scores.apply(score_obs(7, 0, index=1, ts=2.0))
        matchup = DurableMatchupTracker.open(store, external_broadcast_id="vidH")
        matchup.apply(obs("SF", "GB", index=0, ts=0.0))
        matchup.apply(obs("SF", "GB", index=1, ts=2.0))
        game = store.get_game(scores.game_id)
        assert game.pair == (7, 0)
        assert game.confirmed_left_team_id == "SF"
        assert len(store.list_confirmed_changes(game.id)) == 1
        assert len(store.list_matchup_changes(game.id)) == 1
