"""Bounded M11 backend validation: snapshot, SSE, persist, reconnect.

    python tools/m11_validate.py
    LSGC_TEST_DATABASE_URL=postgresql://... python tools/m11_validate.py --postgres

No frontend. No live stream. Tracking is not started by API clients.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from lsgc.api.app import create_app  # noqa: E402
from lsgc.api.hub import GameEventHub  # noqa: E402
from lsgc.identity.catalog import DEFAULT_CATALOG  # noqa: E402
from lsgc.persist.models import PlatformLifecycle  # noqa: E402
from api_support import (  # noqa: E402
    FakeOrchestrator,
    collect_live_sse,
    make_settings,
    matchup_change,
    parse_sse_text,
    score_change,
    upsert,
)
from persist_memory import InMemoryGameRepository  # noqa: E402


def _run(store, hub, label: str) -> None:
    from fastapi.testclient import TestClient

    app = create_app(
        store=store,
        hub=hub,
        settings=make_settings(),
        catalog=DEFAULT_CATALOG,
        orchestrator=FakeOrchestrator(),
    )
    game = upsert(store, "m11ValidateLive", PlatformLifecycle.UPCOMING)
    client = TestClient(app)
    with client:
        snapshot = client.get(f"/api/games/{game.id}").json()
        assert snapshot["score"]["status"] == "UNCONFIRMED"
        assert snapshot["matchup"]["status"] == "UNCONFIRMED"

        def mutate() -> None:
            store.persist_matchup_confirmation(game.id, matchup_change("SF", "NYG"))
            store.persist_confirmation(game.id, score_change(1, 7, 0))
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

        events, _ = collect_live_sse(client.app, game.id, min_events=4, mutate=mutate)
        received = [item[0] for item in events]
        assert "SCORE_CONFIRMED" in received
        assert "MATCHUP_CONFIRMED" in received
        assert "PLATFORM_LIFECYCLE_CHANGED" in received
        store.persist_confirmation(game.id, score_change(2, 14, 0, 7, 0))
        recovered = client.get(f"/api/games/{game.id}").json()
        timeline = client.get(f"/api/games/{game.id}/score-events").json()
        again, _ = parse_sse_text(
            client.get(f"/api/games/{game.id}/events", params={"once": True}).text
        )
        assert recovered["score"]["revision"] == 2
        assert recovered["score"]["status"] == "TRUSTED"
        assert recovered["platform_lifecycle"] == "LIVE"
        assert [item["revision"] for item in timeline["events"]] == [1, 2]
        assert again[0][1]["score_revision"] == 2
        health = client.get("/health").json()
        assert health["process"] == "alive"
    print(f"M11 validation {label}: PASS (revision=2, matchup=SF/NYG, lifecycle=LIVE)")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--postgres":
        from lsgc.persist import PooledGameRepository, apply_migrations, connect, open_pool
        from lsgc.persist.config import postgres_test_dsn

        dsn = postgres_test_dsn()
        if not dsn:
            print("LSGC_TEST_DATABASE_URL is not set", file=sys.stderr)
            return 2
        with connect(dsn) as conn:
            apply_migrations(conn)
            conn.execute(
                "TRUNCATE confirmed_matchup_changes, confirmed_score_changes, games CASCADE"
            )
            conn.commit()
        hub = GameEventHub(DEFAULT_CATALOG)
        store = PooledGameRepository(open_pool(dsn), publisher=hub)
        try:
            _run(store, hub, "postgres")
        finally:
            store.close()
        return 0
    hub = GameEventHub(DEFAULT_CATALOG)
    store = InMemoryGameRepository(publisher=hub)
    _run(store, hub, "memory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
