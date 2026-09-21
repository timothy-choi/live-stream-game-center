"""Seed representative games and serve the M11 API for frontend validation.

    python tools/m12_demo_api.py

No YouTube credentials. Tracking workers are not started.
Hiding the browser embed does not stop anything because nothing is tracking.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import uvicorn  # noqa: E402
from persist_memory import InMemoryGameRepository  # noqa: E402

from lsgc.api.app import create_app  # noqa: E402
from lsgc.api.config import ApiSettings  # noqa: E402
from lsgc.api.hub import GameEventHub  # noqa: E402
from lsgc.identity.catalog import DEFAULT_CATALOG  # noqa: E402
from lsgc.identity.models import ConfirmedMatchupChange, IdentityMethod  # noqa: E402
from lsgc.persist.models import PLATFORM_YOUTUBE, PlatformLifecycle  # noqa: E402
from lsgc.track.models import ConfirmedScoreChange  # noqa: E402

CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"


def _score(revision: int, left: int, right: int, prev_l=None, prev_r=None) -> ConfirmedScoreChange:
    return ConfirmedScoreChange(
        revision=revision,
        previous_left=prev_l,
        previous_right=prev_r,
        new_left=left,
        new_right=right,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=float(revision),
        confirmed_frame_index=revision,
        confirmed_epoch=1,
        initialization=revision == 1,
    )


def _matchup(left: str, right: str) -> ConfirmedMatchupChange:
    return ConfirmedMatchupChange(
        revision=1,
        left_team_id=left,
        right_team_id=right,
        left_method=IdentityMethod.LOGO,
        right_method=IdentityMethod.LOGO,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=True,
    )


def seed(store: InMemoryGameRepository) -> str:
    now = datetime.now(tz=UTC)
    live = store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="hBblAZD-ApY",
        channel_id=CHANNEL,
        title="Madden 27 Arcade",
        platform_lifecycle=PlatformLifecycle.LIVE,
        scheduled_start_at=now - timedelta(hours=1),
        actual_start_at=now - timedelta(hours=1),
        actual_end_at=None,
        synced_at=now,
    )
    store.persist_matchup_confirmation(live.id, _matchup("SF", "GB"))
    store.persist_confirmation(live.id, _score(1, 0, 0))
    store.persist_confirmation(live.id, _score(2, 7, 0, 0, 0))
    store.persist_confirmation(live.id, _score(3, 21, 14, 7, 0))

    store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="upcomingDemo1",
        channel_id=CHANNEL,
        title="Later Arcade broadcast",
        platform_lifecycle=PlatformLifecycle.UPCOMING,
        scheduled_start_at=now + timedelta(hours=2),
        actual_start_at=None,
        actual_end_at=None,
        synced_at=now,
    )

    ended = store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="G2kMfIDFNIQ",
        channel_id=CHANNEL,
        title="Completed Arcade broadcast",
        platform_lifecycle=PlatformLifecycle.ENDED,
        scheduled_start_at=now - timedelta(hours=5),
        actual_start_at=now - timedelta(hours=5),
        actual_end_at=now - timedelta(hours=3),
        synced_at=now,
    )
    store.persist_matchup_confirmation(ended.id, _matchup("LAC", "SF"))
    store.persist_confirmation(ended.id, _score(1, 0, 0))
    store.persist_confirmation(ended.id, _score(2, 24, 56, 0, 0))
    store.mark_acquisition_ended(ended.id)

    conflict = store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="conflictDemo1",
        channel_id=CHANNEL,
        title="Identity review example",
        platform_lifecycle=PlatformLifecycle.LIVE,
        scheduled_start_at=now - timedelta(minutes=20),
        actual_start_at=now - timedelta(minutes=20),
        actual_end_at=None,
        synced_at=now,
    )
    store.persist_matchup_confirmation(conflict.id, _matchup("SF", "NYG"))
    store.persist_confirmation(conflict.id, _score(1, 14, 7))
    store.record_matchup_conflict(conflict.id, ("LAC", "LV"))
    return live.id


def main() -> int:
    hub = GameEventHub(DEFAULT_CATALOG)
    store = InMemoryGameRepository(publisher=hub)
    live_id = seed(store)

    def bump() -> None:
        time.sleep(20)
        store.persist_confirmation(live_id, _score(4, 28, 14, 21, 14))
        print(f"demo score bump applied for {live_id} -> 28-14")

    threading.Thread(target=bump, daemon=True).start()
    settings = ApiSettings(
        cors_origins=("http://localhost:5173", "http://127.0.0.1:5173"),
        run_orchestrator=False,
        heartbeat_s=15.0,
        max_queue=32,
    )
    app = create_app(
        store=store,
        hub=hub,
        settings=settings,
        catalog=DEFAULT_CATALOG,
    )

    @app.post("/__demo/confirm-score")
    def confirm_score(game_id: str, left: int, right: int) -> dict[str, object]:
        """Validation-only hook. Not part of the M11 product API."""
        target = live_id if game_id == "live" else UUID(game_id)
        game = store.get_game(target)
        revision = game.score_revision + 1
        store.persist_confirmation(
            target,
            _score(revision, left, right, game.trusted_left_score, game.trusted_right_score),
        )
        return {"ok": True, "game_id": str(target), "left": left, "right": right, "revision": revision}

    print("M12 demo API on http://127.0.0.1:8000  (in-memory, CORS for Vite)")
    print(f"live game id: {live_id}")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
