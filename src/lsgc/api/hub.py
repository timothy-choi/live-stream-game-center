"""In-process game-event hub.

Persistence notifies this hub after a successful durable write. A missing or
slow subscriber never blocks tracking. Process restart drops ephemeral
notifications; clients recover from PostgreSQL snapshots and score history.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from lsgc.api.mapping import game_cursor, game_summary, require_utc_iso
from lsgc.api.schemas import GameEvent, GameEventType
from lsgc.identity.catalog import TeamCatalog
from lsgc.persist.models import Game

OverflowCallback = Callable[[UUID], None]


def _event_type(kind: str) -> GameEventType:
    try:
        return GameEventType(kind)
    except ValueError:
        return GameEventType.GAME_SNAPSHOT


@dataclass
class Subscriber:
    game_id: UUID
    mailbox: queue.Queue[GameEvent | None]
    overflowed: bool = False


@dataclass
class GameEventHub:
    catalog: TeamCatalog
    max_queue: int = 32
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _subscribers: dict[UUID, list[Subscriber]] = field(default_factory=dict)
    _sequences: dict[UUID, int] = field(default_factory=dict)

    def subscriber_count(self, game_id: UUID | None = None) -> int:
        with self._lock:
            if game_id is None:
                return sum(len(items) for items in self._subscribers.values())
            return len(self._subscribers.get(game_id, ()))

    def next_sequence(self, game_id: UUID) -> int:
        with self._lock:
            current = self._sequences.get(game_id, 0) + 1
            self._sequences[game_id] = current
            return current

    def subscribe(self, game_id: UUID) -> Subscriber:
        subscriber = Subscriber(
            game_id=game_id,
            mailbox=queue.Queue(maxsize=self.max_queue),
        )
        with self._lock:
            self._subscribers.setdefault(game_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            items = self._subscribers.get(subscriber.game_id)
            if not items:
                return
            self._subscribers[subscriber.game_id] = [
                item for item in items if item is not subscriber
            ]
            if not self._subscribers[subscriber.game_id]:
                self._subscribers.pop(subscriber.game_id, None)

    def snapshot_event(self, game: Game) -> GameEvent:
        return self.build_event(GameEventType.GAME_SNAPSHOT.value, game)

    def build_event(
        self,
        kind: str,
        game: Game,
        extra: dict[str, Any] | None = None,
        *,
        sequence: int | None = None,
    ) -> GameEvent:
        summary = game_summary(game, self.catalog)
        payload: dict[str, Any] = {"game": summary.model_dump(mode="json")}
        if extra:
            payload.update(extra)
        return GameEvent(
            event_id=str(uuid4()),
            sequence=sequence if sequence is not None else self.next_sequence(game.id),
            game_id=game.id,
            type=_event_type(kind),
            occurred_at=require_utc_iso(datetime.now(tz=UTC)),
            score_revision=game.score_revision,
            matchup_revision=game.matchup_revision,
            cursor=game_cursor(game),
            payload=payload,
        )

    def resync_event(self, game_id: UUID) -> GameEvent:
        return GameEvent(
            event_id=str(uuid4()),
            sequence=self.next_sequence(game_id),
            game_id=game_id,
            type=GameEventType.RESYNC_REQUIRED,
            occurred_at=require_utc_iso(datetime.now(tz=UTC)),
            score_revision=0,
            matchup_revision=0,
            cursor="resync",
            payload={"reason": "subscriber_queue_overflow"},
        )

    def publish_game_change(
        self, kind: str, game: Game, extra: dict[str, Any] | None = None
    ) -> None:
        """Best-effort fan-out. Never waits on a subscriber queue."""
        event = self.build_event(kind, game, extra)
        with self._lock:
            targets = list(self._subscribers.get(game.id, ()))
        for subscriber in targets:
            if subscriber.overflowed:
                continue
            try:
                subscriber.mailbox.put_nowait(event)
            except queue.Full:
                subscriber.overflowed = True
                try:
                    subscriber.mailbox.put_nowait(None)
                except queue.Full:
                    pass

    def wait(
        self, subscriber: Subscriber, timeout_s: float
    ) -> GameEvent | None:
        try:
            return subscriber.mailbox.get(timeout=timeout_s)
        except queue.Empty:
            return None
