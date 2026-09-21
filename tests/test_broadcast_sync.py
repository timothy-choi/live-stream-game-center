"""Channel sync: exact-ID upsert, idempotency, API-failure isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from lsgc.broadcast.models import PLATFORM_YOUTUBE, PlatformLifecycle
from lsgc.broadcast.sync import ChannelSynchronizer
from tests.broadcast_fakes import CHANNEL_A, CHANNEL_B, FakeCatalog, FakeClock, discovered
from tests.persist_memory import InMemoryGameRepository

START = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)


def _sync(catalog, store, clock=None) -> ChannelSynchronizer:
    return ChannelSynchronizer(catalog, store, clock=clock or FakeClock())


def test_scheduled_then_live_updates_same_row() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [
            discovered(
                "ABC123",
                lifecycle=PlatformLifecycle.UPCOMING,
                title="49ers game",
                scheduled_start=START,
            )
        ]
    )
    sync = _sync(catalog, store)
    first = sync.sync_channel(CHANNEL_A)
    assert first.ok
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    original_id = game.id
    assert game.platform_lifecycle is PlatformLifecycle.UPCOMING
    assert game.pair is None

    catalog.broadcasts = [
        discovered(
            "ABC123",
            lifecycle=PlatformLifecycle.LIVE,
            title="49ers game LIVE",
            scheduled_start=START,
            actual_start=START,
        )
    ]
    second = sync.sync_channel(CHANNEL_A)
    assert second.ok
    updated = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert updated is not None
    assert updated.id == original_id
    assert updated.platform_lifecycle is PlatformLifecycle.LIVE
    assert updated.title == "49ers game LIVE"
    assert updated.actual_start_at == START
    assert len(store.list_games_for_channel(CHANNEL_A)) == 1


def test_repeated_sync_is_idempotent() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [
            discovered("AAA", lifecycle=PlatformLifecycle.UPCOMING),
            discovered("BBB", lifecycle=PlatformLifecycle.LIVE, title="now"),
        ]
    )
    sync = _sync(catalog, store)
    first = sync.sync_channel(CHANNEL_A)
    second = sync.sync_channel(CHANNEL_A)
    assert first.ok and second.ok
    assert len(store.list_games_for_channel(CHANNEL_A)) == 2
    assert second.upserted == 2


def test_already_live_without_prior_schedule() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [discovered("LIVE1", lifecycle=PlatformLifecycle.LIVE, title="unscheduled")]
    )
    report = _sync(catalog, store).sync_channel(CHANNEL_A)
    assert report.live_ids == ("LIVE1",)
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "LIVE1")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.LIVE
    assert game.scheduled_start_at is None


def test_wrong_channel_results_are_rejected() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [
            discovered("OURS", channel_id=CHANNEL_A, lifecycle=PlatformLifecycle.LIVE),
            discovered("THEIRS", channel_id=CHANNEL_B, lifecycle=PlatformLifecycle.LIVE),
        ]
    )
    report = _sync(catalog, store).sync_channel(CHANNEL_A)
    assert report.rejected_wrong_channel == 1
    assert store.get_game_by_external_id(PLATFORM_YOUTUBE, "OURS") is not None
    assert store.get_game_by_external_id(PLATFORM_YOUTUBE, "THEIRS") is None


def test_temporary_api_failure_leaves_durable_state() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [discovered("ABC123", lifecycle=PlatformLifecycle.LIVE, title="keep")]
    )
    sync = _sync(catalog, store)
    assert sync.sync_channel(CHANNEL_A).ok
    before = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert before is not None
    catalog.fail = True
    report = sync.sync_channel(CHANNEL_A)
    assert not report.ok
    assert report.error is not None
    assert "secret-token-value" not in report.error
    after = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert after is not None
    assert after.id == before.id
    assert after.platform_lifecycle is PlatformLifecycle.LIVE
    assert after.title == "keep"


def test_missing_search_hit_does_not_infer_ended() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)],
        refresh={},
    )
    sync = _sync(catalog, store)
    sync.sync_channel(CHANNEL_A)
    catalog.broadcasts = []
    report = sync.sync_channel(CHANNEL_A)
    assert report.ok
    assert catalog.refresh_calls  # attempted videos.list refresh
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.LIVE


def test_refresh_can_mark_ended_when_api_returns_end() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sync = _sync(catalog, store)
    sync.sync_channel(CHANNEL_A)
    catalog.broadcasts = []
    catalog.refresh = {
        "ABC123": discovered(
            "ABC123",
            lifecycle=PlatformLifecycle.ENDED,
            actual_end=START,
        )
    }
    report = sync.sync_channel(CHANNEL_A)
    assert report.ok
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    assert game.platform_lifecycle is PlatformLifecycle.ENDED


def test_metadata_sync_does_not_touch_trusted_scores() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog([discovered("ABC123", lifecycle=PlatformLifecycle.LIVE)])
    sync = _sync(catalog, store)
    sync.sync_channel(CHANNEL_A)
    game = store.get_game_by_external_id(PLATFORM_YOUTUBE, "ABC123")
    assert game is not None
    from lsgc.track.models import ConfirmedScoreChange

    change = ConfirmedScoreChange(
        revision=1,
        previous_left=None,
        previous_right=None,
        new_left=7,
        new_right=0,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=True,
    )
    store.persist_confirmation(game.id, change)
    catalog.broadcasts = [
        discovered("ABC123", lifecycle=PlatformLifecycle.LIVE, title="retitled")
    ]
    sync.sync_channel(CHANNEL_A)
    updated = store.get_game(game.id)
    assert updated.pair == (7, 0)
    assert updated.score_revision == 1
    assert updated.title == "retitled"
    assert store.list_confirmed_changes(game.id)[0].new_pair == (7, 0)


def test_all_upcoming_broadcasts_are_kept() -> None:
    store = InMemoryGameRepository()
    catalog = FakeCatalog(
        [
            discovered(f"U{i}", lifecycle=PlatformLifecycle.UPCOMING, title=f"g{i}")
            for i in range(6)
        ]
    )
    _sync(catalog, store).sync_channel(CHANNEL_A)
    games = store.list_games_for_channel(CHANNEL_A)
    assert len(games) == 6
    assert {g.external_broadcast_id for g in games} == {f"U{i}" for i in range(6)}
