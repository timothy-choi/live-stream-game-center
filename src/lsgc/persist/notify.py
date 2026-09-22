"""Best-effort change notification after durable writes.

Tracking/persistence must not depend on subscribers. A publisher exception is
swallowed so a slow or missing API hub cannot block M8/M10 writes.
"""

from __future__ import annotations

from typing import Any, Protocol

from lsgc.persist.models import Game

KIND_SCORE_CONFIRMED = "SCORE_CONFIRMED"
KIND_MATCHUP_CONFIRMED = "MATCHUP_CONFIRMED"
KIND_MATCHUP_CONFLICT = "MATCHUP_CONFLICT"
KIND_PLATFORM_LIFECYCLE_CHANGED = "PLATFORM_LIFECYCLE_CHANGED"
KIND_TRACKING_STATUS_CHANGED = "TRACKING_STATUS_CHANGED"
KIND_COMPLETION_CHANGED = "COMPLETION_CHANGED"
KIND_FINAL_SCORE_CONFLICT = "FINAL_SCORE_CONFLICT"


class GameChangePublisher(Protocol):
    def publish_game_change(
        self, kind: str, game: Game, extra: dict[str, Any] | None = None
    ) -> None: ...


def emit_game_change(
    publisher: GameChangePublisher | None,
    kind: str,
    game: Game,
    extra: dict[str, Any] | None = None,
) -> None:
    if publisher is None:
        return
    try:
        publisher.publish_game_change(kind, game, extra)
    except Exception:
        return
