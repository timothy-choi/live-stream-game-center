"""Tracking eligibility, supervisor, restart, and no-viewer orchestration."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import numpy as np

from lsgc.broadcast.calibration import MappingCalibrationLookup
from lsgc.broadcast.eligibility import evaluate_eligibility
from lsgc.broadcast.models import (
    PLATFORM_YOUTUBE,
    ConnectedChannel,
    EligibilityReason,
    PlatformLifecycle,
    WorkerState,
)
from lsgc.broadcast.orchestrator import BroadcastOrchestrator
from lsgc.broadcast.supervisor import TrackingSupervisor
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.live.state import SessionState
from lsgc.models import Frame
from lsgc.persist.models import TrackingStatus
from tests.broadcast_fakes import (
    CHANNEL_A,
    CHANNEL_B,
    DummyCalibration,
    FakeCatalog,
    FakeClock,
    RecordingSessionFactory,
    absence_pipeline_factory,
    discovered,
    readable_obs,
)
from tests.persist_memory import InMemoryGameRepository

CHANNEL = ConnectedChannel(channel_id=CHANNEL_A)


def wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


def _frame(index: int, ts: float) -> Frame:
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    return Frame(
        index=index,
        image=image,
        width=2,
        height=2,
        timestamp_s=ts,
        source_url="https://www.youtube.com/watch?v=ABC123",
        received_at=datetime.now(tz=UTC),
        source_id="ABC123",
        extra={"source_epoch": 1},
    )


class ConfirmingPipeline:
    def __init__(self, pair: tuple[int, int] = (7, 0)) -> None:
        self.pair = pair

    def observe_frame(self, frame: Frame):
        return readable_obs(frame, self.pair[0], self.pair[1])


def _orchestrator(
    catalog: FakeCatalog,
    store: InMemoryGameRepository,
    *,
    session_factory=None,
    pipeline_factory=None,
    clock: FakeClock | None = None,
    retry_initial_s: float = 15.0,
    retry_max_s: float = 300.0,
    calibration_channel: str = CHANNEL_A,
):
    clock = clock or FakeClock()
    lookup = MappingCalibrationLookup(
        {calibration_channel: DummyCalibration(calibration_channel)}
    )
    supervisor = TrackingSupervisor(
        store,
        lookup,
        session_factory=session_factory
        or RecordingSessionFactory(hold=threading.Event()),
        pipeline_factory=pipeline_factory or absence_pipeline_factory,
        retry_initial_s=retry_initial_s,
        retry_max_s=retry_max_s,
        clock=clock,
    )
    return BroadcastOrchestrator(
        [CHANNEL],
        ChannelSynchronizer(catalog, store, clock=clock),
        supervisor,
        store,
        lookup,
        interval_s=30.0,
        clock=clock,
    )


def test_missing_calibration_is_not_eligible() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="ABC123", channel_id=CHANNEL_A
    )
    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="ABC123",
        channel_id=CHANNEL_A,
        title="x",
        platform_lifecycle=PlatformLifecycle.LIVE,
        scheduled_start_at=None,
        actual_start_at=None,
        actual_end_at=None,
        synced_at=datetime.now(tz=UTC),
    )
    game = store.get_game(game.id)
    decision = evaluate_eligibility(
        game, expected_channel_id=CHANNEL_A, calibration=MappingCalibrationLookup()
    )
    assert not decision.eligible
    assert decision.reason is EligibilityReason.MISSING_CALIBRATION
    assert decision.detail and "NOT_READY" in decision.detail


def test_tracking_disabled_is_not_eligible() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="ABC123", channel_id=CHANNEL_A
    )
    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="ABC123",
        channel_id=CHANNEL_A,
        title="x",
        platform_lifecycle=PlatformLifecycle.LIVE,
        scheduled_start_at=None,
        actual_start_at=None,
        actual_end_at=None,
        synced_at=datetime.now(tz=UTC),
    )
    store.set_tracking_enabled(game.id, False)
    game = store.get_game(game.id)
    lookup = MappingCalibrationLookup({CHANNEL_A: DummyCalibration(CHANNEL_A)})
    decision = evaluate_eligibility(
        game, expected_channel_id=CHANNEL_A, calibration=lookup
    )
    assert decision.reason is EligibilityReason.TRACKING_DISABLED


def test_no_viewer_reconcile_starts_exactly_one_worker() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    status = orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("ABC123"))
    assert status.active_worker_count == 1 or orch.supervisor.is_active("ABC123")
    assert len(sessions.created) == 1
    orch.reconcile_once()
    wait_until(lambda: len(sessions.created) == 1)
    assert len(orch.supervisor.active_ids()) == 1
    assert len(sessions.created) == 1
    orch.shutdown()


def test_already_live_and_unscheduled_live_start_workers() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [discovered("DIRECT", lifecycle=PlatformLifecycle.LIVE, title="no prior row")]
    )
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("DIRECT"))
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "DIRECT")
    assert game is not None
    assert game.scheduled_start_at is None
    orch.shutdown()


def test_tracking_disabled_prevents_and_stops_worker() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="ABC123",
        channel_id=CHANNEL_A,
        title="t",
        platform_lifecycle=PlatformLifecycle.LIVE,
        scheduled_start_at=None,
        actual_start_at=None,
        actual_end_at=None,
        synced_at=datetime.now(tz=UTC),
    )
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    store.set_tracking_enabled(game.id, False)
    orch.reconcile_once()
    time.sleep(0.05)
    assert not orch.supervisor.is_active("ABC123")
    store.set_tracking_enabled(game.id, True)
    orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("ABC123"))
    store.set_tracking_enabled(game.id, False)
    orch.reconcile_once()
    wait_until(lambda: not orch.supervisor.is_active("ABC123"))
    still = store.get_game(game.id)
    assert still.tracking_status is TrackingStatus.TRACKING
    assert still.platform_lifecycle is PlatformLifecycle.LIVE
    orch.shutdown()


def test_disable_does_not_delete_score_history() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    frames = (_frame(0, 0.0), _frame(1, 2.0))
    sessions = RecordingSessionFactory(frames=frames, terminal=SessionState.ENDED)
    orch = _orchestrator(
        catalog,
        store,
        session_factory=sessions,
        pipeline_factory=lambda _c: ConfirmingPipeline((14, 7)),
    )
    orch.reconcile_once()
    wait_until(
        lambda: store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123") is not None
        and store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123").score_revision >= 1
    )
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    history_before = store.list_confirmed_changes(game.id)
    assert history_before
    store.set_tracking_enabled(game.id, False)
    orch.reconcile_once()
    wait_until(lambda: not orch.supervisor.is_active("ABC123"))
    after = store.get_game(game.id)
    assert after.pair == game.pair
    assert store.list_confirmed_changes(game.id) == history_before
    orch.shutdown()


def test_ended_broadcast_stops_worker_and_is_not_final() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("ABC123"))
    catalog.broadcasts = [
        discovered("ABC123", lifecycle=PlatformLifecycle.ENDED, actual_end=datetime.now(tz=UTC))
    ]
    orch.reconcile_once()
    wait_until(lambda: not orch.supervisor.is_active("ABC123"))
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.ENDED
    assert game.tracking_status is TrackingStatus.ACQUISITION_ENDED
    assert not hasattr(game, "final_score")
    assert "FINAL" not in TrackingStatus.__members__
    assert "FINAL" not in PlatformLifecycle.__members__
    orch.shutdown()


def test_api_failure_does_not_kill_running_worker() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("ABC123"))
    catalog.fail = True
    status = orch.reconcile_once()
    assert not status.last_sync_ok
    assert orch.supervisor.is_active("ABC123")
    orch.shutdown()


def test_worker_failure_uses_backoff_not_tight_loop() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    clock = FakeClock()
    sessions = RecordingSessionFactory(fail=True, terminal=SessionState.FAILED)
    orch = _orchestrator(
        catalog, store, session_factory=sessions, clock=clock, retry_initial_s=30.0
    )
    orch.reconcile_once()
    wait_until(lambda: sessions.created)
    wait_until(lambda: not orch.supervisor.is_active("ABC123"))
    orch.supervisor.reap()
    first_starts = len(sessions.created)
    orch.reconcile_once()
    time.sleep(0.05)
    assert len(sessions.created) == first_starts
    clock.advance(31)
    orch.reconcile_once()
    wait_until(lambda: len(sessions.created) > first_starts)
    orch.shutdown()


def test_process_restart_registry_empty_then_one_worker() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    first_sessions = RecordingSessionFactory(hold=threading.Event())
    first = _orchestrator(catalog, store, session_factory=first_sessions)
    first.reconcile_once()
    wait_until(lambda: first.supervisor.is_active("ABC123"))
    first.shutdown()
    assert first.supervisor.active_ids() == frozenset()

    second_sessions = RecordingSessionFactory(hold=threading.Event())
    second = _orchestrator(catalog, store, session_factory=second_sessions)
    assert second.supervisor.active_ids() == frozenset()
    second.reconcile_once()
    wait_until(lambda: second.supervisor.is_active("ABC123"))
    assert len(second.supervisor.active_ids()) == 1
    second.reconcile_once()
    assert len(second_sessions.created) == 1
    second.shutdown()


def test_multiple_broadcasts_get_independent_workers() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [
            discovered("LIVE1", lifecycle=PlatformLifecycle.LIVE),
            discovered("LIVE2", lifecycle=PlatformLifecycle.LIVE),
        ]
    )
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: len(orch.supervisor.active_ids()) == 2)
    assert orch.supervisor.is_active("LIVE1")
    assert orch.supervisor.is_active("LIVE2")
    orch.shutdown()


def test_wrong_channel_calibration_is_never_substituted() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [discovered("ABC123", channel_id=CHANNEL_A, lifecycle=PlatformLifecycle.LIVE)]
    )
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(
        catalog,
        store,
        session_factory=sessions,
        calibration_channel=CHANNEL_B,
    )
    orch.reconcile_once()
    time.sleep(0.05)
    assert not orch.supervisor.is_active("ABC123")
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    orch.shutdown()


def test_m4_ended_while_youtube_still_live_does_not_mark_final() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(terminal=SessionState.ENDED)
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: sessions.created)
    wait_until(lambda: not orch.supervisor.is_active("ABC123"))
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.LIVE
    assert game.tracking_status is TrackingStatus.TRACKING
    orch.shutdown()


def test_worker_cleanup_removes_registry_entry() -> None:
    store = InMemoryGameRepository()
    lookup = MappingCalibrationLookup({CHANNEL_A: DummyCalibration(CHANNEL_A)})
    hold = threading.Event()
    supervisor = TrackingSupervisor(
        store,
        lookup,
        session_factory=RecordingSessionFactory(hold=hold),
        pipeline_factory=absence_pipeline_factory,
    )
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="ABC123", channel_id=CHANNEL_A
    )
    supervisor.ensure_tracking(game)
    wait_until(lambda: supervisor.is_active("ABC123"))
    supervisor.ensure_tracking(game)
    assert len(supervisor.active_ids()) == 1
    supervisor.stop_tracking("ABC123")
    wait_until(lambda: not supervisor.is_active("ABC123"))
    supervisor.reap()
    snap = {item.broadcast_id: item for item in supervisor.snapshots()}["ABC123"]
    assert snap.state in {WorkerState.STOPPED, WorkerState.CANCELLED}


def test_run_forever_stops_on_event() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([])
    orch = _orchestrator(catalog, store, session_factory=RecordingSessionFactory())
    stop = threading.Event()
    waited = []

    def wait(event, seconds):
        waited.append(seconds)
        event.set()
        return True

    orch._wait = wait  # type: ignore[method-assign]
    orch.run_forever(stop)
    assert waited == [30.0]


def test_cli_parser_has_sync_and_run() -> None:
    from lsgc.broadcast.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["sync"]).command == "sync"
    assert parser.parse_args(["run"]).command == "run"


def test_config_allows_empty_channels_for_oauth_onboarding() -> None:
    from lsgc.broadcast.config import load_connected_channels

    assert load_connected_channels(environ={}) == ()
    channels = load_connected_channels(
        environ={"LSGC_YOUTUBE_CHANNEL_ID": "UC_CHANNEL_A"}
    )
    assert channels[0].channel_id == "UC_CHANNEL_A"
