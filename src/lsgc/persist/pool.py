"""Concurrent-safe PostgreSQL access for the HTTP API.

M9 workers may keep one serialized connection. FastAPI requests must not share
it. Each operation checks a connection out of a small pool and returns it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from psycopg import ClientCursor
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from lsgc.persist.models import Game, MatchupPersistResult, PersistResult, PlatformLifecycle, ScoreChangeRecord
from lsgc.persist.notify import GameChangePublisher
from lsgc.persist.repository import PostgresGameRepository
from lsgc.identity.models import ConfirmedMatchupChange
from lsgc.track.models import ConfirmedScoreChange
from lsgc.youtube_oauth.models import YoutubeConnection
from lsgc.youtube_oauth.repository import PostgresYoutubeConnectionRepository


def open_pool(dsn: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "cursor_factory": ClientCursor},
        open=True,
    )


class PooledGameRepository:
    """Forwards repository methods using one pooled connection per call."""

    def __init__(
        self,
        pool: ConnectionPool,
        publisher: GameChangePublisher | None = None,
    ) -> None:
        self._pool = pool
        self._publisher = publisher

    @contextmanager
    def _repo(self) -> Iterator[PostgresGameRepository]:
        with self._pool.connection() as conn:
            yield PostgresGameRepository(conn, publisher=self._publisher)

    @contextmanager
    def _connections(self) -> Iterator[PostgresYoutubeConnectionRepository]:
        with self._pool.connection() as conn:
            yield PostgresYoutubeConnectionRepository(conn)

    def ping(self) -> bool:
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
        return True

    def get_game(self, game_id: UUID) -> Game:
        with self._repo() as repo:
            return repo.get_game(game_id)

    def get_game_by_external_id(self, platform: str, external_broadcast_id: str) -> Game | None:
        with self._repo() as repo:
            return repo.get_game_by_external_id(platform, external_broadcast_id)

    def list_games_for_channel(self, channel_id: str) -> tuple[Game, ...]:
        with self._repo() as repo:
            return repo.list_games_for_channel(channel_id)

    def list_games(
        self,
        *,
        channel_id: str | None = None,
        lifecycles: tuple[PlatformLifecycle, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[Game, ...], int]:
        with self._repo() as repo:
            return repo.list_games(
                channel_id=channel_id,
                lifecycles=lifecycles,
                limit=limit,
                offset=offset,
            )

    def list_confirmed_changes(self, game_id: UUID) -> tuple[ConfirmedScoreChange, ...]:
        with self._repo() as repo:
            return repo.list_confirmed_changes(game_id)

    def list_score_change_records(
        self,
        game_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ScoreChangeRecord, ...]:
        with self._repo() as repo:
            return repo.list_score_change_records(game_id, limit=limit, offset=offset)

    def list_matchup_changes(self, game_id: UUID) -> tuple[ConfirmedMatchupChange, ...]:
        with self._repo() as repo:
            return repo.list_matchup_changes(game_id)

    def create_or_get_game(self, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.create_or_get_game(**kwargs)

    def persist_confirmation(self, game_id: UUID, change: ConfirmedScoreChange) -> PersistResult:
        with self._repo() as repo:
            return repo.persist_confirmation(game_id, change)

    def persist_matchup_confirmation(
        self, game_id: UUID, change: ConfirmedMatchupChange
    ) -> MatchupPersistResult:
        with self._repo() as repo:
            return repo.persist_matchup_confirmation(game_id, change)

    def record_matchup_conflict(self, game_id: UUID, conflict_pair: tuple[str, str]) -> Game:
        with self._repo() as repo:
            return repo.record_matchup_conflict(game_id, conflict_pair)

    def upsert_broadcast_metadata(self, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.upsert_broadcast_metadata(**kwargs)

    def set_tracking_enabled(self, game_id: UUID, enabled: bool) -> Game:
        with self._repo() as repo:
            return repo.set_tracking_enabled(game_id, enabled)

    def mark_acquisition_ended(self, game_id: UUID) -> Game:
        with self._repo() as repo:
            return repo.mark_acquisition_ended(game_id)

    def record_worker_finished(self, game_id: UUID, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.record_worker_finished(game_id, **kwargs)

    def enter_finalizing(self, game_id: UUID, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.enter_finalizing(game_id, **kwargs)

    def record_postgame_score(self, game_id: UUID, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.record_postgame_score(game_id, **kwargs)

    def record_recovery_status(self, game_id: UUID, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.record_recovery_status(game_id, **kwargs)

    def mark_score_stable(self, game_id: UUID, **kwargs: Any) -> Game:
        with self._repo() as repo:
            return repo.mark_score_stable(game_id, **kwargs)

    def mark_final(self, game_id: UUID) -> Game:
        with self._repo() as repo:
            return repo.mark_final(game_id)

    def list_connections(self) -> tuple[YoutubeConnection, ...]:
        with self._connections() as repo:
            return repo.list_connections()

    def list_active_youtube_connections(self) -> tuple[YoutubeConnection, ...]:
        with self._connections() as repo:
            return repo.list_active_youtube_connections()

    def get_connection(self, connection_id: UUID) -> YoutubeConnection:
        with self._connections() as repo:
            return repo.get_connection(connection_id)

    def get_active_connection_for_channel(self, channel_id: str) -> YoutubeConnection | None:
        with self._connections() as repo:
            return repo.get_active_connection_for_channel(channel_id)

    def upsert_connected_channel(self, **kwargs: Any) -> YoutubeConnection:
        with self._connections() as repo:
            return repo.upsert_connected_channel(**kwargs)

    def update_tokens(self, connection_id: UUID, **kwargs: Any) -> YoutubeConnection:
        with self._connections() as repo:
            return repo.update_tokens(connection_id, **kwargs)

    def mark_needs_reauthorization(self, connection_id: UUID) -> YoutubeConnection:
        with self._connections() as repo:
            return repo.mark_needs_reauthorization(connection_id)

    def mark_disconnected(self, connection_id: UUID) -> YoutubeConnection:
        with self._connections() as repo:
            return repo.mark_disconnected(connection_id)

    def create_oauth_state(self, state: str, *, expires_at) -> object:
        with self._connections() as repo:
            return repo.create_oauth_state(state, expires_at=expires_at)

    def consume_oauth_state(self, state: str, *, now=None) -> object:
        with self._connections() as repo:
            return repo.consume_oauth_state(state, now=now)

    def close(self) -> None:
        self._pool.close()
