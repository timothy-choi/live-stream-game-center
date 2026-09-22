"""YouTube ENDED must not cancel FINALIZING, and ENDED alone is not FINAL."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from lsgc.broadcast.eligibility import evaluate_eligibility
from lsgc.broadcast.models import EligibilityReason, PLATFORM_YOUTUBE, PlatformLifecycle
from lsgc.persist.models import CompletionStatus, TrackingStatus
from persist_memory import InMemoryGameRepository
from tests.broadcast_fakes import (
    CHANNEL_A,
    DummyCalibration,
    FakeCatalog,
    RecordingSessionFactory,
    discovered,
)
from tests.test_broadcast_orchestrator import _orchestrator, wait_until
from lsgc.broadcast.calibration import MappingCalibrationLookup


def test_ended_without_completion_is_not_final() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="ABC123", channel_id=CHANNEL_A
    )
    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="ABC123",
        channel_id=CHANNEL_A,
        title="x",
        platform_lifecycle=PlatformLifecycle.ENDED,
        scheduled_start_at=None,
        actual_start_at=None,
        actual_end_at=datetime.now(tz=UTC),
        synced_at=datetime.now(tz=UTC),
    )
    game = store.get_game(game.id)
    lookup = MappingCalibrationLookup({CHANNEL_A: DummyCalibration(CHANNEL_A)})
    decision = evaluate_eligibility(game, expected_channel_id=CHANNEL_A, calibration=lookup)
    assert not decision.eligible
    assert decision.reason is EligibilityReason.NOT_LIVE
    assert game.completion_status is CompletionStatus.UNVERIFIED


def test_finalizing_remains_eligible_after_ended() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="ABC123", channel_id=CHANNEL_A
    )
    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="ABC123",
        channel_id=CHANNEL_A,
        title="x",
        platform_lifecycle=PlatformLifecycle.ENDED,
        scheduled_start_at=None,
        actual_start_at=None,
        actual_end_at=datetime.now(tz=UTC),
        synced_at=datetime.now(tz=UTC),
    )
    store.enter_finalizing(
        game.id,
        confirmed_timestamp_s=10.0,
        confirmed_frame_index=4,
        deadline_at=datetime.now(tz=UTC),
    )
    game = store.get_game(game.id)
    lookup = MappingCalibrationLookup({CHANNEL_A: DummyCalibration(CHANNEL_A)})
    decision = evaluate_eligibility(game, expected_channel_id=CHANNEL_A, calibration=lookup)
    assert decision.eligible
    assert game.completion_status is CompletionStatus.FINALIZING


def test_orchestrator_does_not_stop_finalizing_worker_on_ended() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sessions = RecordingSessionFactory(hold=threading.Event())
    orch = _orchestrator(catalog, store, session_factory=sessions)
    orch.reconcile_once()
    wait_until(lambda: orch.supervisor.is_active("ABC123"))
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    store.enter_finalizing(
        game.id,
        confirmed_timestamp_s=10.0,
        confirmed_frame_index=4,
        deadline_at=datetime.now(tz=UTC),
    )
    catalog.broadcasts = [
        discovered("ABC123", lifecycle=PlatformLifecycle.ENDED, actual_end=datetime.now(tz=UTC))
    ]
    orch.reconcile_once()
    assert orch.supervisor.is_active("ABC123")
    game = store.get_game(game.id)
    assert game.platform_lifecycle is PlatformLifecycle.ENDED
    assert game.completion_status is CompletionStatus.FINALIZING
    assert game.tracking_status is TrackingStatus.TRACKING
    sessions.hold.set()
    orch.shutdown()
