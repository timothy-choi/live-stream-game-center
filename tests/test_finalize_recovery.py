"""Missed gameplay scoring recovery: video evidence, not inferred scoring."""

from __future__ import annotations

from lsgc.api.hub import GameEventHub
from lsgc.api.mapping import game_summary, timeline_event
from lsgc.finalize.policy import FinalizationPolicy
from lsgc.finalize.recovery import FrameSequenceSource, MemoryFrameWindow
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist.finalizer import DurableFinalizationTracker, postgame_observation
from lsgc.persist.models import (
    PLATFORM_YOUTUBE,
    CompletionStatus,
    HistoryCompleteness,
    RecoveryStatus,
)
from lsgc.score.models import ScoreRead
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance
from lsgc.vision.detector import DetectionResult
from persist_memory import InMemoryGameRepository
from tests.finalize_support import layout_a
from tests.recovery_support import gameplay_pipeline, held_score_frames, score_frame

PROFILE, PIPELINE = gameplay_pipeline()


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


def _seed(store: InMemoryGameRepository, left: int, right: int, *, timestamp_s: float = 10.0):
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="recoveryVideo01",
        channel_id="UC_recovery_channel",
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
            first_seen_timestamp_s=timestamp_s - 2.0,
            first_seen_frame_index=0,
            confirmed_timestamp_s=timestamp_s,
            confirmed_frame_index=1,
            confirmed_epoch=1,
            initialization=True,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    return store.get_game(game.id)


def _open(store, game, frames):
    return DurableFinalizationTracker.open(
        store,
        game.id,
        policy=FinalizationPolicy(stability_s=8.0, recovery_window_s=90.0, recovery_scan_interval_s=0.5),
        recovery_source=FrameSequenceSource(frames),
        gameplay_pipeline=PIPELINE,
    )


def _confirm(tracker, left: int, right: int, *, t0: float = 20.0) -> None:
    tracker.observe_presence(True, timestamp_s=t0, frame_index=80)
    tracker.observe_presence(True, timestamp_s=t0 + 2.0, frame_index=81)
    tracker.observe_postgame_score(_obs(left, right, t0 + 2.0, 81))
    tracker.observe_postgame_score(_obs(left, right, t0 + 4.0, 82))


def _pairs(store, game_id):
    return [(item.new_pair, item.source) for item in store.list_confirmed_changes(game_id)]


def test_a_nothing_missed_skips_recovery() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 49)
    tracker = _open(store, game, [])
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    game = store.get_game(game.id)
    assert game.recovery_status is RecoveryStatus.NOT_NEEDED
    assert game.score_history_completeness is HistoryCompleteness.COMPLETE
    assert game.completion_status is CompletionStatus.FINAL
    assert _pairs(store, game.id) == [
        ((35, 49), ScoreProvenance.GAMEPLAY_SCOREBOARD),
    ]


def test_qxsvhobkdhq_live_already_35_49_is_not_needed() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="qxsVHobKdhQ",
        channel_id="UCz3q0f-dMPNIyQhntPTE7gQ",
    )
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=1,
            previous_left=None,
            previous_right=None,
            new_left=35,
            new_right=49,
            evidence_count=2,
            first_seen_timestamp_s=0.0,
            first_seen_frame_index=0,
            confirmed_timestamp_s=10.0,
            confirmed_frame_index=1,
            confirmed_epoch=1,
            initialization=True,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    tracker = DurableFinalizationTracker.open(store, game.id)
    _confirm(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.recovery_status is RecoveryStatus.NOT_NEEDED
    assert game.pair == (35, 49)


def test_b_one_missed_state() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 42)
    frames = held_score_frames(PROFILE, [(35, 42), (35, 49)], start_s=10.0, until_s=24.0)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    game = store.get_game(game.id)
    history = store.list_confirmed_changes(game.id)
    assert [(item.new_pair, item.source) for item in history] == [
        ((35, 42), ScoreProvenance.GAMEPLAY_SCOREBOARD),
        ((35, 49), ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD),
    ]
    assert history[-1].confirmed_timestamp_s is not None
    assert history[-1].confirmed_timestamp_s >= 10.0
    assert game.recovery_status is RecoveryStatus.COMPLETE
    assert game.score_history_completeness is HistoryCompleteness.COMPLETE
    assert game.completion_status is CompletionStatus.FINAL


def test_c_multiple_missed_states() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(
        PROFILE,
        [(35, 35), (35, 41), (35, 42), (35, 48), (35, 49)],
        start_s=10.0,
        until_s=24.0,
    )
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    history = store.list_confirmed_changes(game.id)
    recovered = [item for item in history if item.source is ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD]
    assert [item.new_pair for item in recovered] == [(35, 41), (35, 42), (35, 48), (35, 49)]
    assert [item.confirmed_timestamp_s for item in recovered] == sorted(
        item.confirmed_timestamp_s for item in recovered
    )
    assert all(item.source is not ScoreProvenance.POSTGAME_FINAL for item in history)
    game = store.get_game(game.id)
    assert game.recovery_status is RecoveryStatus.COMPLETE
    assert game.completion_status is CompletionStatus.FINAL


def test_d_both_sides_changed() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 21, 21)
    frames = held_score_frames(
        PROFILE,
        [(21, 21), (21, 27), (21, 28), (27, 28), (28, 28), (28, 31)],
        start_s=10.0,
        until_s=24.0,
    )
    tracker = _open(store, game, frames)
    _confirm(tracker, 28, 31)
    recovered = [
        item.new_pair
        for item in store.list_confirmed_changes(game.id)
        if item.source is ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD
    ]
    assert recovered == [(21, 27), (21, 28), (27, 28), (28, 28), (28, 31)]


def test_e_duplicate_observations_one_transition() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 27, 42)
    frames = held_score_frames(
        PROFILE,
        [(27, 42), (27, 42), (33, 42), (33, 42), (35, 42), (35, 48), (35, 48), (35, 49)],
        start_s=10.0,
        hold=2,
        until_s=24.0,
    )
    tracker = _open(store, game, frames)
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=2,
            previous_left=27,
            previous_right=42,
            new_left=33,
            new_right=42,
            evidence_count=2,
            first_seen_timestamp_s=11.0,
            first_seen_frame_index=2,
            confirmed_timestamp_s=11.5,
            confirmed_frame_index=3,
            confirmed_epoch=1,
            initialization=False,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=3,
            previous_left=33,
            previous_right=42,
            new_left=35,
            new_right=42,
            evidence_count=2,
            first_seen_timestamp_s=12.0,
            first_seen_frame_index=4,
            confirmed_timestamp_s=12.5,
            confirmed_frame_index=5,
            confirmed_epoch=1,
            initialization=False,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    game = store.get_game(game.id)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    history = store.list_confirmed_changes(game.id)
    pairs = [item.new_pair for item in history]
    assert pairs == [(27, 42), (33, 42), (35, 42), (35, 48), (35, 49)]
    assert [item.source for item in history[3:]] == [
        ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD,
        ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD,
    ]


def test_f_single_ocr_noise_is_not_persisted() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(PROFILE, [(35, 35)], start_s=10.0)
    noise = score_frame(PROFILE, 99, 99, index=20, timestamp_s=11.1)
    later = held_score_frames(
        PROFILE, [(35, 41), (35, 49)], start_s=12.0, hold=4, index0=21, until_s=24.0
    )
    tracker = _open(store, game, frames + [noise] + later)
    _confirm(tracker, 35, 49)
    recovered = [
        item.new_pair
        for item in store.list_confirmed_changes(game.id)
        if item.source is ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD
    ]
    assert (99, 99) not in recovered
    assert recovered == [(35, 41), (35, 49)]


def test_g_partial_recovery_appends_postgame_terminal() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(PROFILE, [(35, 35), (35, 42)], start_s=10.0)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    history = store.list_confirmed_changes(game.id)
    assert [(item.new_pair, item.source) for item in history] == [
        ((35, 35), ScoreProvenance.GAMEPLAY_SCOREBOARD),
        ((35, 42), ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD),
        ((35, 49), ScoreProvenance.POSTGAME_FINAL),
    ]
    game = store.get_game(game.id)
    assert game.completion_status is CompletionStatus.FINAL
    assert game.recovery_status is RecoveryStatus.PARTIAL
    assert game.score_history_completeness is HistoryCompleteness.PARTIAL


def test_h_recovery_unavailable() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    tracker = DurableFinalizationTracker.open(store, game.id)
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    history = store.list_confirmed_changes(game.id)
    assert history[-1].source is ScoreProvenance.POSTGAME_FINAL
    game = store.get_game(game.id)
    assert game.recovery_status is RecoveryStatus.UNAVAILABLE
    assert game.score_history_completeness is HistoryCompleteness.UNAVAILABLE
    assert game.completion_status is CompletionStatus.FINAL


def test_i_decreasing_evidence_is_conflict() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(PROFILE, [(35, 35), (28, 35)], start_s=10.0)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.final_score_conflict
    assert game.recovery_status is RecoveryStatus.CONFLICT
    tracker.maybe_finalize(timestamp_s=40.0)
    assert store.get_game(game.id).completion_status is not CompletionStatus.FINAL
    assert (28, 35) not in [item.new_pair for item in store.list_confirmed_changes(game.id)]


def test_j_restart_does_not_duplicate_recovered_revisions() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(
        PROFILE, [(35, 35), (35, 41), (35, 42), (35, 48), (35, 49)], start_s=10.0, until_s=24.0
    )
    first = _open(store, game, frames)
    _confirm(first, 35, 49)
    before = store.list_confirmed_changes(game.id)
    restored = _open(store, game, frames)
    _confirm(restored, 35, 49)
    after = store.list_confirmed_changes(game.id)
    assert [item.revision for item in after] == [item.revision for item in before]
    assert [item.new_pair for item in after] == [item.new_pair for item in before]
    restored.maybe_finalize(timestamp_s=32.0)
    assert store.get_game(game.id).completion_status is CompletionStatus.FINAL


def test_partial_coverage_is_reported() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35, timestamp_s=1.0)
    frames = held_score_frames(PROFILE, [(35, 42), (35, 49)], start_s=10.0)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    game = store.get_game(game.id)
    assert game.recovery_status is RecoveryStatus.PARTIAL
    assert game.score_history_completeness is HistoryCompleteness.PARTIAL
    assert game.pair == (35, 49)


def test_api_exposes_recovery_and_evidence_time() -> None:
    store = InMemoryGameRepository()
    game = _seed(store, 35, 35)
    frames = held_score_frames(PROFILE, [(35, 35), (35, 41), (35, 49)], start_s=10.0, until_s=24.0)
    tracker = _open(store, game, frames)
    _confirm(tracker, 35, 49)
    game = store.get_game(game.id)
    summary = game_summary(game, DEFAULT_CATALOG)
    assert summary.recovery_status == "COMPLETE"
    assert summary.score_history_completeness == "COMPLETE"
    records = store.list_score_change_records(game.id)
    recovered = [timeline_event(item) for item in records if item.change.source is ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD]
    assert recovered[0].source == "RECOVERED_GAMEPLAY_SCOREBOARD"
    assert recovered[0].observed_timestamp_s is not None
    assert recovered[0].confirmed_at.endswith("Z")


def test_sse_recovered_events_precede_final() -> None:
    hub = GameEventHub(DEFAULT_CATALOG)
    store = InMemoryGameRepository(publisher=hub)
    game = _seed(store, 35, 42)
    frames = held_score_frames(PROFILE, [(35, 42), (35, 49)], start_s=10.0, until_s=24.0)
    tracker = DurableFinalizationTracker.open(
        store,
        game.id,
        policy=FinalizationPolicy(stability_s=8.0),
        recovery_source=FrameSequenceSource(frames),
        gameplay_pipeline=PIPELINE,
    )
    sub = hub.subscribe(game.id)
    _confirm(tracker, 35, 49)
    tracker.maybe_finalize(timestamp_s=32.0)
    kinds = []
    for _ in range(16):
        event = hub.wait(sub, 0.2)
        if event is None:
            break
        kinds.append(event.type.value)
    assert "SCORE_CONFIRMED" in kinds
    assert kinds.index("SCORE_CONFIRMED") < max(
        i for i, kind in enumerate(kinds) if kind == "COMPLETION_CHANGED"
    )
    assert store.get_game(game.id).completion_status is CompletionStatus.FINAL


def test_memory_window_evicts_beyond_bound() -> None:
    window = MemoryFrameWindow(window_s=2.0)
    window.push(score_frame(PROFILE, 1, 1, index=0, timestamp_s=1.0))
    window.push(score_frame(PROFILE, 2, 2, index=1, timestamp_s=4.0))
    scanned = window.scan(start_s=1.0, end_s=4.0, interval_s=0.5)
    assert scanned.coverage.value == "PARTIAL"
    assert all(frame.timestamp_s != 1.0 for frame in scanned.frames)


def test_recovery_source_is_not_youtube_specific() -> None:
    source = FrameSequenceSource(held_score_frames(PROFILE, [(1, 2)], start_s=0.0))
    window = source.scan(start_s=0.0, end_s=1.0, interval_s=0.5)
    assert window.frames
    assert "youtube" not in type(source).__name__.lower()
