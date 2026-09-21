"""Realtime SSE and in-process hub tests. Tracking stays independent."""

from __future__ import annotations

import time
from uuid import uuid4

from lsgc.api.hub import GameEventHub
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist.models import PlatformLifecycle
from api_support import (
    FakeOrchestrator,
    collect_live_sse,
    make_client,
    matchup_change,
    parse_sse_text,
    score_change,
    upsert,
)
from persist_memory import InMemoryGameRepository


def _wired(max_queue: int = 32, heartbeat_s: float = 0.05):
    hub = GameEventHub(DEFAULT_CATALOG, max_queue=max_queue)
    store = InMemoryGameRepository(publisher=hub)
    orch = FakeOrchestrator()
    client = make_client(
        store, hub, orchestrator=orch, max_queue=max_queue, heartbeat_s=heartbeat_s
    )
    return client, store, hub, orch


class TestRealtimeDelivery:
    def test_subscriber_receives_snapshot(self) -> None:
        client, store, _, orch = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 3, 0))
        with client:
            response = client.get(f"/api/games/{game.id}/events", params={"once": True})
        events, _ = parse_sse_text(response.text)
        assert response.headers["content-type"].startswith("text/event-stream")
        assert events[0][0] == "GAME_SNAPSHOT"
        assert events[0][1]["payload"]["game"]["score"]["revision"] == 1
        assert events[0][1]["payload"]["game"]["score"]["left"] == 3
        assert orch.ensure_calls == []

    def test_score_change_delivered_over_sse(self) -> None:
        client, store, _, _ = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            events, _ = collect_live_sse(
                client.app,
                game.id,
                min_events=2,
                mutate=lambda: store.persist_confirmation(game.id, score_change(1, 7, 0)),
            )
        types = [item[0] for item in events]
        assert "GAME_SNAPSHOT" in types
        assert "SCORE_CONFIRMED" in types
        assert any(item[1]["score_revision"] == 1 for item in events)

    def test_matchup_and_lifecycle_via_hub(self) -> None:
        hub = GameEventHub(DEFAULT_CATALOG)
        store = InMemoryGameRepository(publisher=hub)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.UPCOMING)
        sub = hub.subscribe(game.id)
        store.persist_matchup_confirmation(game.id, matchup_change())
        store.upsert_broadcast_metadata(
            platform=game.platform,
            external_broadcast_id=game.external_broadcast_id,
            channel_id=game.channel_id,
            title=game.title,
            platform_lifecycle=PlatformLifecycle.LIVE,
            scheduled_start_at=game.scheduled_start_at,
            actual_start_at=game.actual_start_at,
            actual_end_at=None,
            synced_at=game.updated_at,
        )
        first = hub.wait(sub, 0.5)
        second = hub.wait(sub, 0.5)
        types = {item.type.value for item in (first, second) if item is not None}
        assert "MATCHUP_CONFIRMED" in types
        assert "PLATFORM_LIFECYCLE_CHANGED" in types
        hub.unsubscribe(sub)

    def test_two_subscribers_receive_independently(self) -> None:
        hub = GameEventHub(DEFAULT_CATALOG)
        store = InMemoryGameRepository(publisher=hub)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        a = hub.subscribe(game.id)
        b = hub.subscribe(game.id)
        store.persist_confirmation(game.id, score_change(1, 6, 0))
        ev_a = hub.wait(a, 0.5)
        ev_b = hub.wait(b, 0.5)
        assert ev_a is not None and ev_b is not None
        assert ev_a.type.value == "SCORE_CONFIRMED"
        assert ev_b.type.value == "SCORE_CONFIRMED"
        assert ev_a.score_revision == ev_b.score_revision == 1
        hub.unsubscribe(a)
        store.persist_confirmation(game.id, score_change(2, 7, 0, 6, 0))
        assert hub.wait(a, 0.2) is None
        ev_b2 = hub.wait(b, 0.5)
        assert ev_b2 is not None
        assert ev_b2.score_revision == 2
        hub.unsubscribe(b)

    def test_zero_subscribers_persist_still_works(self) -> None:
        client, store, hub, _ = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        assert hub.subscriber_count(game.id) == 0
        store.persist_confirmation(game.id, score_change(1, 14, 0))
        with client:
            body = client.get(f"/api/games/{game.id}").json()
        assert body["score"]["revision"] == 1
        assert body["score"]["left"] == 14

    def test_disconnect_all_does_not_stop_tracking(self) -> None:
        client, store, hub, orch = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            client.get(f"/api/games/{game.id}/events", params={"once": True})
            assert hub.subscriber_count(game.id) == 0
            store.persist_confirmation(game.id, score_change(1, 10, 3))
            body = client.get(f"/api/games/{game.id}").json()
        assert body["score"]["revision"] == 1
        assert orch.ensure_calls == []

    def test_subscription_does_not_start_tracking(self) -> None:
        client, store, _, orch = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            client.get(f"/api/games/{game.id}/events", params={"once": True})
        assert orch.ensure_calls == []

    def test_unknown_game_events_404(self) -> None:
        client, _, _, _ = _wired()
        with client:
            response = client.get(f"/api/games/{uuid4()}/events")
        assert response.status_code == 404


class TestReconnectAndOverflow:
    def test_lost_wakeup_snapshot_after_subscribe(self) -> None:
        client, store, _, _ = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 0, 0))
        with client:
            snapshot = client.get(f"/api/games/{game.id}").json()
            assert snapshot["score"]["revision"] == 1
            store.persist_confirmation(game.id, score_change(2, 7, 0, 0, 0))
            response = client.get(f"/api/games/{game.id}/events", params={"once": True})
        events, _ = parse_sse_text(response.text)
        assert events[0][0] == "GAME_SNAPSHOT"
        assert events[0][1]["score_revision"] == 2
        assert events[0][1]["payload"]["game"]["score"]["left"] == 7

    def test_reconnect_catches_up_from_durable_state(self) -> None:
        client, store, _, _ = _wired()
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            client.get(f"/api/games/{game.id}/events", params={"once": True})
            store.persist_confirmation(game.id, score_change(1, 3, 0))
            store.persist_confirmation(game.id, score_change(2, 7, 0, 3, 0))
            snapshot = client.get(f"/api/games/{game.id}").json()
            timeline = client.get(f"/api/games/{game.id}/score-events").json()
            response = client.get(f"/api/games/{game.id}/events", params={"once": True})
        events, _ = parse_sse_text(response.text)
        assert snapshot["score"]["revision"] == 2
        assert [item["revision"] for item in timeline["events"]] == [1, 2]
        assert events[0][1]["score_revision"] == 2

    def test_sequence_identifies_stale_or_duplicate(self) -> None:
        hub = GameEventHub(DEFAULT_CATALOG)
        store = InMemoryGameRepository(publisher=hub)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        store.persist_confirmation(game.id, score_change(1, 3, 0))
        first = hub.snapshot_event(store.get_game(game.id))
        store.persist_confirmation(game.id, score_change(2, 7, 0, 3, 0))
        second = hub.snapshot_event(store.get_game(game.id))
        assert second.sequence > first.sequence
        assert second.score_revision >= first.score_revision
        assert first.cursor != second.cursor

    def test_slow_subscriber_cannot_block_persist(self) -> None:
        hub = GameEventHub(DEFAULT_CATALOG, max_queue=2)
        store = InMemoryGameRepository(publisher=hub)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        subscriber = hub.subscribe(game.id)
        store.persist_confirmation(game.id, score_change(1, 0, 0))
        store.persist_confirmation(game.id, score_change(2, 3, 0, 0, 0))
        started = time.monotonic()
        store.persist_confirmation(game.id, score_change(3, 7, 0, 3, 0))
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert store.get_game(game.id).score_revision == 3
        assert subscriber.overflowed or subscriber.mailbox.qsize() <= 2

    def test_queue_overflow_resync(self) -> None:
        hub = GameEventHub(DEFAULT_CATALOG, max_queue=1)
        store = InMemoryGameRepository(publisher=hub)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        subscriber = hub.subscribe(game.id)
        store.persist_confirmation(game.id, score_change(1, 0, 0))
        store.persist_confirmation(game.id, score_change(2, 3, 0, 0, 0))
        store.persist_confirmation(game.id, score_change(3, 7, 0, 3, 0))
        assert subscriber.overflowed
        event = hub.resync_event(game.id)
        assert event.type.value == "RESYNC_REQUIRED"

    def test_publisher_failure_does_not_block_persist(self) -> None:
        class Boom:
            def publish_game_change(self, kind, game, extra=None) -> None:
                raise RuntimeError("hub exploded")

        store = InMemoryGameRepository(publisher=Boom())
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        result = store.persist_confirmation(game.id, score_change(1, 7, 3))
        assert result.game.score_revision == 1

    def test_heartbeat_comment(self) -> None:
        client, store, _, _ = _wired(heartbeat_s=0.05)
        game = upsert(store, "liveGameId0001", PlatformLifecycle.LIVE)
        with client:
            events, heartbeats = collect_live_sse(client.app, game.id, min_events=0)
        assert heartbeats >= 1 or events
        with client:
            _, heartbeats = collect_live_sse(client.app, game.id, min_events=0)
        assert heartbeats >= 1
