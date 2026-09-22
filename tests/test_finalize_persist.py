"""Durable FINALIZING / FINAL, provenance, restart, and invariant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lsgc.finalize.policy import FinalizationPolicy
from lsgc.persist.errors import SchemaError
from lsgc.persist.finalizer import DurableFinalizationTracker, postgame_observation
from lsgc.persist.models import PLATFORM_YOUTUBE, CompletionStatus
from lsgc.score.models import ScoreRead
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance
from lsgc.vision.detector import DetectionResult
from persist_memory import InMemoryGameRepository
from tests.finalize_support import layout_a


def _detection() -> DetectionResult:
    _image, profile, _reference, _ = layout_a()
    return DetectionResult(
        present=True,
        score=0.9,
        threshold=0.55,
        anchors=(),
        frame_width=1280,
        frame_height=720,
        channel_id=profile.channel_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
    )


def _obs(left: int, right: int, timestamp_s: float, frame_index: int):
    return postgame_observation(
        left=ScoreRead.of(left, confidence=0.95, margin=0.2, raw_digits=str(left)),
        right=ScoreRead.of(right, confidence=0.95, margin=0.2, raw_digits=str(right)),
        detection=_detection(),
        frame_index=frame_index,
        timestamp_s=timestamp_s,
    )


def _seed_durable(store: InMemoryGameRepository, left: int, right: int):
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="qxsVHobKdhQ", channel_id="UC_final_a"
    )
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=1,
            previous_left=None,
            previous_right=None,
            new_left=left,
            new_right=right,
            evidence_count=2,
            first_seen_timestamp_s=0.0,
            first_seen_frame_index=0,
            confirmed_timestamp_s=2.0,
            confirmed_frame_index=1,
            confirmed_epoch=1,
            initialization=True,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    return store.get_game(game.id)


def _confirm_postgame(tracker: DurableFinalizationTracker, left: int, right: int) -> None:
    tracker.observe_presence(True, timestamp_s=100.0, frame_index=10)
    tracker.observe_presence(True, timestamp_s=102.0, frame_index=11)
    tracker.observe_postgame_score(_obs(left, right, 102.0, 11))
    tracker.observe_postgame_score(_obs(left, right, 104.0, 12))


def test_case_a_final_after_stability() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 49)
    tracker = DurableFinalizationTracker.open(
        store, game.id, policy=FinalizationPolicy(stability_s=8.0)
    )
    _confirm_postgame(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.completion_status is CompletionStatus.FINALIZING
    assert game.postgame_pair == (35, 49)
    tracker.maybe_finalize(timestamp_s=104.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINALIZING
    tracker.maybe_finalize(timestamp_s=112.0)
    final = store.get_game(game.id)
    assert final.completion_status is CompletionStatus.FINAL
    assert final.final_score_verified
    assert final.pair == (35, 49)
    assert final.recovery_status.value == "NOT_NEEDED"
    assert final.score_history_completeness.value == "COMPLETE"


def test_case_b_updates_with_postgame_provenance() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 42)
    tracker = DurableFinalizationTracker.open(store, game.id)
    _confirm_postgame(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.pair == (35, 49)
    history = store.list_confirmed_changes(game.id)
    assert history[-1].source is ScoreProvenance.POSTGAME_FINAL
    assert history[-1].new_pair == (35, 49)
    assert game.recovery_status.value == "UNAVAILABLE"
    assert game.score_history_completeness.value == "UNAVAILABLE"
    tracker.maybe_finalize(timestamp_s=112.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINAL


def test_case_c_establishes_when_uninitialized() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="noScoreYet", channel_id="UC_final_a"
    )
    tracker = DurableFinalizationTracker.open(store, game.id)
    _confirm_postgame(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.pair == (35, 49)
    assert store.list_confirmed_changes(game.id)[-1].source is ScoreProvenance.POSTGAME_FINAL
    assert game.recovery_status.value == "UNAVAILABLE"
    tracker.maybe_finalize(timestamp_s=112.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINAL


def test_case_d_conflict_is_not_final() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 49)
    tracker = DurableFinalizationTracker.open(store, game.id)
    _confirm_postgame(tracker, 35, 42)
    game = store.get_game(game.id)
    assert game.pair == (35, 49)
    assert game.final_score_conflict
    assert game.completion_status is CompletionStatus.FINALIZING
    tracker.maybe_finalize(timestamp_s=200.0)
    assert store.get_game(game.id).completion_status is not CompletionStatus.FINAL


def test_case_e_inconsistent_postgame_does_not_final() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 49)
    tracker = DurableFinalizationTracker.open(store, game.id)
    tracker.observe_presence(True, timestamp_s=100.0, frame_index=10)
    tracker.observe_presence(True, timestamp_s=102.0, frame_index=11)
    tracker.observe_postgame_score(_obs(35, 42, 102.0, 11))
    tracker.observe_postgame_score(_obs(21, 7, 104.0, 12))
    game = store.get_game(game.id)
    assert game.postgame_pair is None
    assert game.completion_status is CompletionStatus.FINALIZING
    tracker.maybe_finalize(timestamp_s=200.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINALIZING


def test_cannot_mark_final_before_score_agrees() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 42)
    deadline = datetime.now(tz=UTC) + timedelta(seconds=35)
    store.enter_finalizing(
        game.id,
        confirmed_timestamp_s=10.0,
        confirmed_frame_index=4,
        deadline_at=deadline,
    )
    with pytest.raises(SchemaError):
        store.mark_final(game.id)


def test_restart_restores_finalizing_without_rediscovery() -> None:
    store = InMemoryGameRepository()
    game = _seed_durable(store, 35, 49)
    first = DurableFinalizationTracker.open(store, game.id)
    _confirm_postgame(first, 35, 49)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINALIZING
    restored = DurableFinalizationTracker.open(store, game.id)
    restored.maybe_finalize(timestamp_s=112.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINAL
    assert store.get_game(game.id).pair == (35, 49)
