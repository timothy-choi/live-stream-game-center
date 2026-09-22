"""M11 exposes YouTube lifecycle separately from completion; SSE ordering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lsgc.api.hub import GameEventHub
from lsgc.api.mapping import game_summary
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist.models import PLATFORM_YOUTUBE, CompletionStatus, PlatformLifecycle
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance
from persist_memory import InMemoryGameRepository
from tests.api_support import upsert


def test_summary_separates_ended_from_final() -> None:
    store = InMemoryGameRepository()
    game = upsert(store, "endedVideo0001", PlatformLifecycle.ENDED)
    summary = game_summary(game, DEFAULT_CATALOG)
    assert summary.platform_lifecycle == "ENDED"
    assert summary.completion_status == "UNVERIFIED"
    assert summary.final_score_verified is False


def test_sse_score_before_final() -> None:
    hub = GameEventHub(DEFAULT_CATALOG)
    store = InMemoryGameRepository(publisher=hub)
    game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=1,
            previous_left=None,
            previous_right=None,
            new_left=35,
            new_right=42,
            evidence_count=2,
            first_seen_timestamp_s=0.0,
            first_seen_frame_index=0,
            confirmed_timestamp_s=2.0,
            confirmed_frame_index=1,
            confirmed_epoch=1,
            initialization=True,
        ),
    )
    sub = hub.subscribe(game.id)
    store.enter_finalizing(
        game.id,
        confirmed_timestamp_s=10.0,
        confirmed_frame_index=5,
        deadline_at=datetime.now(tz=UTC) + timedelta(seconds=35),
    )
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=2,
            previous_left=35,
            previous_right=42,
            new_left=35,
            new_right=49,
            evidence_count=2,
            first_seen_timestamp_s=12.0,
            first_seen_frame_index=6,
            confirmed_timestamp_s=14.0,
            confirmed_frame_index=7,
            confirmed_epoch=1,
            initialization=False,
            source=ScoreProvenance.POSTGAME_FINAL,
        ),
    )
    store.record_postgame_score(
        game.id,
        left=35,
        right=49,
        confirmed_timestamp_s=14.0,
        conflict=None,
        reset_stability=True,
        stable_timestamp_s=14.0,
    )
    store.mark_final(game.id)
    kinds = []
    scores = []
    for _ in range(8):
        event = hub.wait(sub, 0.2)
        if event is None:
            break
        kinds.append(event.type.value)
        payload_game = event.payload["game"]
        scores.append((payload_game["score"]["left"], payload_game["score"]["right"]))
        if event.type.value == "COMPLETION_CHANGED" and payload_game["completion_status"] == "FINAL":
            assert payload_game["score"]["left"] == 35
            assert payload_game["score"]["right"] == 49
            assert payload_game["final_score_verified"] is True
    assert "SCORE_CONFIRMED" in kinds
    assert kinds.count("COMPLETION_CHANGED") >= 2
    final_index = max(
        i for i, kind in enumerate(kinds) if kind == "COMPLETION_CHANGED"
    )
    # The FINAL completion event must not carry a stale pair.
    assert scores[final_index] == (35, 49)
    game = store.get_game(game.id)
    assert game.completion_status is CompletionStatus.FINAL
    assert game.platform_lifecycle is PlatformLifecycle.LIVE
