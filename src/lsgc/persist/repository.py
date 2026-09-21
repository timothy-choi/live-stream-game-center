"""PostgreSQL repository for durable games and confirmed score history.

M7 validator and M4/M6 contain no SQL.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from lsgc.persist.errors import (
    ConflictingConfirmationError,
    ConflictingMatchupError,
    PersistError,
    SchemaError,
    StaleConfirmationError,
    UnknownGameError,
)
from lsgc.persist.notify import (
    KIND_MATCHUP_CONFIRMED,
    KIND_MATCHUP_CONFLICT,
    KIND_PLATFORM_LIFECYCLE_CHANGED,
    KIND_SCORE_CONFIRMED,
    KIND_TRACKING_STATUS_CHANGED,
    GameChangePublisher,
    emit_game_change,
)
from lsgc.persist.models import (
    Game,
    MatchupPersistResult,
    PersistOutcome,
    PersistResult,
    PlatformLifecycle,
    ScoreChangeRecord,
    TrackingStatus,
)
from lsgc.identity.models import ConfirmedMatchupChange, IdentityMethod
from lsgc.track.models import ConfirmedScoreChange

_GAME_COLUMNS = """
    id, platform, external_broadcast_id, channel_id, tracking_status,
    trusted_left_score, trusted_right_score, score_revision,
    last_confirmed_at, last_confirmed_timestamp_s,
    last_confirmed_frame_index, last_confirmed_epoch,
    created_at, updated_at,
    title, platform_lifecycle, scheduled_start_at, actual_start_at,
    actual_end_at, last_synced_at, tracking_enabled,
    last_tracking_error, last_worker_finished_at,
    confirmed_left_team_id, confirmed_right_team_id, matchup_revision,
    matchup_confirmed_at, matchup_confirmed_timestamp_s,
    matchup_confirmed_frame_index, matchup_confirmed_epoch,
    matchup_left_method, matchup_right_method,
    matchup_conflict_count, last_matchup_conflict
"""


def event_payload_matches(stored: ConfirmedScoreChange, incoming: ConfirmedScoreChange) -> bool:
    """Identical confirmation retry vs conflicting same-revision payload."""
    return (
        stored.revision == incoming.revision
        and stored.previous_left == incoming.previous_left
        and stored.previous_right == incoming.previous_right
        and stored.new_left == incoming.new_left
        and stored.new_right == incoming.new_right
        and stored.initialization == incoming.initialization
        and stored.evidence_count == incoming.evidence_count
        and stored.first_seen_timestamp_s == incoming.first_seen_timestamp_s
        and stored.first_seen_frame_index == incoming.first_seen_frame_index
        and stored.confirmed_timestamp_s == incoming.confirmed_timestamp_s
        and stored.confirmed_frame_index == incoming.confirmed_frame_index
        and stored.confirmed_epoch == incoming.confirmed_epoch
    )


def matchup_payload_matches(
    stored: ConfirmedMatchupChange, incoming: ConfirmedMatchupChange
) -> bool:
    return (
        stored.revision == incoming.revision
        and stored.left_team_id == incoming.left_team_id
        and stored.right_team_id == incoming.right_team_id
        and stored.left_method == incoming.left_method
        and stored.right_method == incoming.right_method
        and stored.evidence_count == incoming.evidence_count
        and stored.first_seen_timestamp_s == incoming.first_seen_timestamp_s
        and stored.first_seen_frame_index == incoming.first_seen_frame_index
        and stored.confirmed_timestamp_s == incoming.confirmed_timestamp_s
        and stored.confirmed_frame_index == incoming.confirmed_frame_index
        and stored.confirmed_epoch == incoming.confirmed_epoch
    )


def validate_broadcast_id(platform: str, external_broadcast_id: str) -> None:
    if not platform or not platform.strip():
        raise PersistError("platform must not be empty")
    identity = (external_broadcast_id or "").strip()
    if not identity:
        raise PersistError("external_broadcast_id must not be empty")
    lowered = identity.lower()
    if "://" in identity or "youtube.com" in lowered or "youtu.be" in lowered:
        raise PersistError(
            "external_broadcast_id must be the stable video/broadcast id, not a URL"
        )


def _serialized(method):
    """One-at-a-time connection use. Outermost call commits visibility."""

    def wrapped(self, *args, **kwargs):
        with self._lock:
            self._depth += 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self._depth -= 1
                if (
                    self._depth == 0
                    and not self._conn.closed
                    and not self._conn.autocommit
                ):
                    try:
                        self._conn.commit()
                    except Exception:
                        self._conn.rollback()

    return wrapped


class PostgresGameRepository:
    """SQL for games + confirmed_score_changes. One connection, caller-owned.

    Optional `publisher` is notified after successful durable mutations. It
    must not be required for persistence; failures there are ignored.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        publisher: GameChangePublisher | None = None,
    ) -> None:
        self._conn = conn
        self._lock = threading.RLock()
        self._depth = 0
        self._publisher = publisher

    @_serialized
    def create_or_get_game(
        self,
        *,
        platform: str,
        external_broadcast_id: str,
        channel_id: str | None = None,
    ) -> Game:
        validate_broadcast_id(platform, external_broadcast_id)
        identity = external_broadcast_id.strip()
        now = datetime.now(tz=UTC)
        game_id = uuid4()
        try:
            with self._conn.transaction():
                self._conn.execute(
                    f"""
                    INSERT INTO games (
                        id, platform, external_broadcast_id, channel_id,
                        tracking_status, trusted_left_score, trusted_right_score,
                        score_revision, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, NULL, NULL, 0, %s, %s
                    )
                    ON CONFLICT (platform, external_broadcast_id) DO NOTHING
                    """,
                    (
                        game_id,
                        platform,
                        identity,
                        channel_id,
                        TrackingStatus.TRACKING.value,
                        now,
                        now,
                    ),
                )
                if channel_id:
                    self._conn.execute(
                        """
                        UPDATE games
                           SET channel_id = COALESCE(channel_id, %s),
                               updated_at = %s
                         WHERE platform = %s AND external_broadcast_id = %s
                           AND channel_id IS NULL
                        """,
                        (channel_id, now, platform, identity),
                    )
        except UniqueViolation as exc:
            raise PersistError("game identity conflict") from exc
        game = self.get_game_by_external_id(platform, identity)
        if game is None:
            raise PersistError("failed to create or load game")
        return game

    @_serialized
    def get_game(self, game_id: UUID) -> Game:
        row = self._conn.execute(
            f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s",
            (game_id,),
        ).fetchone()
        if row is None:
            raise UnknownGameError(f"game {game_id} not found")
        return _game_from_row(row)

    @_serialized
    def get_game_by_external_id(
        self, platform: str, external_broadcast_id: str
    ) -> Game | None:
        row = self._conn.execute(
            f"""
            SELECT {_GAME_COLUMNS} FROM games
             WHERE platform = %s AND external_broadcast_id = %s
            """,
            (platform, external_broadcast_id.strip()),
        ).fetchone()
        return None if row is None else _game_from_row(row)

    @_serialized
    def list_games_for_channel(self, channel_id: str) -> tuple[Game, ...]:
        rows = self._conn.execute(
            f"""
            SELECT {_GAME_COLUMNS} FROM games
             WHERE channel_id = %s
             ORDER BY scheduled_start_at NULLS LAST, created_at, id
            """,
            (channel_id,),
        ).fetchall()
        return tuple(_game_from_row(row) for row in rows)

    @_serialized
    def list_games(
        self,
        *,
        channel_id: str | None = None,
        lifecycles: tuple[PlatformLifecycle, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[Game, ...], int]:
        """Product list: LIVE, then UPCOMING by start, then recently ENDED."""
        if limit < 1:
            raise PersistError("limit must be >= 1")
        if offset < 0:
            raise PersistError("offset must be >= 0")
        clauses = ["TRUE"]
        params: list[object] = []
        if channel_id:
            clauses.append("channel_id = %s")
            params.append(channel_id)
        if lifecycles:
            clauses.append("platform_lifecycle = ANY(%s)")
            params.append([item.value for item in lifecycles])
        where = " AND ".join(clauses)
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM games WHERE {where}",
            params,
        ).fetchone()
        count = int(total["n"]) if total else 0
        rows = self._conn.execute(
            f"""
            SELECT {_GAME_COLUMNS} FROM games
             WHERE {where}
             ORDER BY
                CASE platform_lifecycle
                    WHEN 'LIVE' THEN 0
                    WHEN 'UPCOMING' THEN 1
                    WHEN 'ENDED' THEN 2
                    ELSE 3
                END,
                CASE WHEN platform_lifecycle = 'UPCOMING' THEN scheduled_start_at END
                    ASC NULLS LAST,
                CASE WHEN platform_lifecycle = 'ENDED'
                     THEN COALESCE(actual_end_at, updated_at) END
                    DESC NULLS LAST,
                created_at DESC,
                id
             LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        ).fetchall()
        return tuple(_game_from_row(row) for row in rows), count

    @_serialized
    def upsert_broadcast_metadata(
        self,
        *,
        platform: str,
        external_broadcast_id: str,
        channel_id: str,
        title: str | None,
        platform_lifecycle: PlatformLifecycle,
        scheduled_start_at: datetime | None,
        actual_start_at: datetime | None,
        actual_end_at: datetime | None,
        synced_at: datetime,
    ) -> Game:
        """Create or update schedule/lifecycle fields. Never writes trusted scores."""
        validate_broadcast_id(platform, external_broadcast_id)
        identity = external_broadcast_id.strip()
        previous = self.get_game_by_external_id(platform, identity)
        previous_lifecycle = None if previous is None else previous.platform_lifecycle
        now = datetime.now(tz=UTC)
        with self._conn.transaction():
            self.create_or_get_game(
                platform=platform,
                external_broadcast_id=identity,
                channel_id=channel_id,
            )
            self._conn.execute(
                """
                UPDATE games SET
                    title = COALESCE(%s, title),
                    platform_lifecycle = %s,
                    scheduled_start_at = COALESCE(%s, scheduled_start_at),
                    actual_start_at = COALESCE(%s, actual_start_at),
                    actual_end_at = COALESCE(%s, actual_end_at),
                    last_synced_at = %s,
                    channel_id = COALESCE(channel_id, %s),
                    updated_at = %s
                 WHERE platform = %s AND external_broadcast_id = %s
                """,
                (
                    title,
                    platform_lifecycle.value,
                    scheduled_start_at,
                    actual_start_at,
                    actual_end_at,
                    synced_at,
                    channel_id,
                    now,
                    platform,
                    identity,
                ),
            )
        game = self.get_game_by_external_id(platform, identity)
        if game is None:
            raise PersistError("failed to upsert broadcast metadata")
        if previous_lifecycle != game.platform_lifecycle:
            emit_game_change(self._publisher, KIND_PLATFORM_LIFECYCLE_CHANGED, game)
        return game

    @_serialized
    def set_tracking_enabled(self, game_id: UUID, enabled: bool) -> Game:
        now = datetime.now(tz=UTC)
        with self._conn.transaction():
            row = self._conn.execute(
                f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                (game_id,),
            ).fetchone()
            if row is None:
                raise UnknownGameError(f"game {game_id} not found")
            self._conn.execute(
                """
                UPDATE games
                   SET tracking_enabled = %s, updated_at = %s
                 WHERE id = %s
                """,
                (enabled, now, game_id),
            )
        game = self.get_game(game_id)
        emit_game_change(self._publisher, KIND_TRACKING_STATUS_CHANGED, game)
        return game

    @_serialized
    def record_worker_finished(
        self,
        game_id: UUID,
        *,
        error: str | None,
        finished_at: datetime | None = None,
    ) -> Game:
        now = datetime.now(tz=UTC)
        finished = finished_at or now
        with self._conn.transaction():
            row = self._conn.execute(
                f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                (game_id,),
            ).fetchone()
            if row is None:
                raise UnknownGameError(f"game {game_id} not found")
            self._conn.execute(
                """
                UPDATE games
                   SET last_tracking_error = %s,
                       last_worker_finished_at = %s,
                       updated_at = %s
                 WHERE id = %s
                """,
                (error, finished, now, game_id),
            )
        return self.get_game(game_id)

    @_serialized
    def list_confirmed_changes(self, game_id: UUID) -> tuple[ConfirmedScoreChange, ...]:
        rows = self._conn.execute(
            """
            SELECT revision, previous_left, previous_right, new_left, new_right,
                   initialization, evidence_count, first_seen_timestamp_s,
                   first_seen_frame_index, confirmed_timestamp_s,
                   confirmed_frame_index, confirmed_epoch
              FROM confirmed_score_changes
             WHERE game_id = %s
             ORDER BY revision
            """,
            (game_id,),
        ).fetchall()
        return tuple(_change_from_row(row) for row in rows)

    @_serialized
    def list_score_change_records(
        self,
        game_id: UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ScoreChangeRecord, ...]:
        self.get_game(game_id)
        if offset < 0:
            raise PersistError("offset must be >= 0")
        if limit is not None and limit < 1:
            raise PersistError("limit must be >= 1")
        sql = """
            SELECT revision, previous_left, previous_right, new_left, new_right,
                   initialization, evidence_count, first_seen_timestamp_s,
                   first_seen_frame_index, confirmed_timestamp_s,
                   confirmed_frame_index, confirmed_epoch, persisted_at
              FROM confirmed_score_changes
             WHERE game_id = %s
             ORDER BY revision
        """
        params: list[object] = [game_id]
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        elif offset:
            sql += " OFFSET %s"
            params.append(offset)
        rows = self._conn.execute(sql, params).fetchall()
        records = []
        for row in rows:
            persisted = row["persisted_at"]
            if persisted.tzinfo is None:
                persisted = persisted.replace(tzinfo=UTC)
            records.append(
                ScoreChangeRecord(change=_change_from_row(row), persisted_at=persisted)
            )
        return tuple(records)

    def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1")
        return True

    @_serialized
    def persist_confirmation(
        self, game_id: UUID, change: ConfirmedScoreChange
    ) -> PersistResult:
        """Append history and update current trusted score in one transaction."""
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                    (game_id,),
                ).fetchone()
                if row is None:
                    raise UnknownGameError(f"game {game_id} not found")
                current = _game_from_row(row)
                if change.revision == current.score_revision:
                    stored = self._load_revision(game_id, change.revision)
                    if stored is not None and event_payload_matches(stored, change):
                        return PersistResult(PersistOutcome.IDEMPOTENT, current, stored)
                    raise ConflictingConfirmationError(
                        f"revision {change.revision} already persisted with different data"
                    )
                if change.revision < current.score_revision:
                    raise StaleConfirmationError(
                        f"revision {change.revision} is older than persisted "
                        f"{current.score_revision}"
                    )
                if change.revision != current.score_revision + 1:
                    raise ConflictingConfirmationError(
                        f"expected revision {current.score_revision + 1}, "
                        f"got {change.revision}"
                    )
                if (
                    change.previous_left != current.trusted_left_score
                    or change.previous_right != current.trusted_right_score
                ):
                    raise ConflictingConfirmationError(
                        "confirmation previous pair does not match current trusted score"
                    )
                result = self._insert_and_update(game_id, change)
            emit_game_change(self._publisher, KIND_SCORE_CONFIRMED, result.game)
            return result
        except UniqueViolation as exc:
            stored = self._load_revision(game_id, change.revision)
            if stored is not None and event_payload_matches(stored, change):
                return PersistResult(
                    PersistOutcome.IDEMPOTENT, self.get_game(game_id), stored
                )
            raise ConflictingConfirmationError(
                f"revision {change.revision} unique conflict"
            ) from exc

    def _insert_and_update(
        self, game_id: UUID, change: ConfirmedScoreChange
    ) -> PersistResult:
        self._conn.execute(
            """
            INSERT INTO confirmed_score_changes (
                id, game_id, revision, previous_left, previous_right,
                new_left, new_right, initialization, evidence_count,
                first_seen_timestamp_s, first_seen_frame_index,
                confirmed_timestamp_s, confirmed_frame_index,
                confirmed_epoch, persisted_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                uuid4(),
                game_id,
                change.revision,
                change.previous_left,
                change.previous_right,
                change.new_left,
                change.new_right,
                change.initialization,
                change.evidence_count,
                change.first_seen_timestamp_s,
                change.first_seen_frame_index,
                change.confirmed_timestamp_s,
                change.confirmed_frame_index,
                change.confirmed_epoch,
                datetime.now(tz=UTC),
            ),
        )
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE games SET
                trusted_left_score = %s,
                trusted_right_score = %s,
                score_revision = %s,
                last_confirmed_at = %s,
                last_confirmed_timestamp_s = %s,
                last_confirmed_frame_index = %s,
                last_confirmed_epoch = %s,
                updated_at = %s
             WHERE id = %s
            """,
            (
                change.new_left,
                change.new_right,
                change.revision,
                now,
                change.confirmed_timestamp_s,
                change.confirmed_frame_index,
                change.confirmed_epoch,
                now,
                game_id,
            ),
        )
        game = self.get_game(game_id)
        return PersistResult(PersistOutcome.APPLIED, game, change)

    @_serialized
    def mark_acquisition_ended(self, game_id: UUID) -> Game:
        now = datetime.now(tz=UTC)
        with self._conn.transaction():
            row = self._conn.execute(
                f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                (game_id,),
            ).fetchone()
            if row is None:
                raise UnknownGameError(f"game {game_id} not found")
            self._conn.execute(
                """
                UPDATE games
                   SET tracking_status = %s, updated_at = %s
                 WHERE id = %s
                """,
                (TrackingStatus.ACQUISITION_ENDED.value, now, game_id),
            )
        game = self.get_game(game_id)
        emit_game_change(self._publisher, KIND_TRACKING_STATUS_CHANGED, game)
        return game

    @_serialized
    def assert_current_matches_history(self, game_id: UUID) -> None:
        """Test/recovery invariant. Not called on every frame."""
        game = self.get_game(game_id)
        history = self.list_confirmed_changes(game_id)
        if game.score_revision == 0:
            if game.pair is not None:
                raise SchemaError("revision 0 must have NULL trusted scores")
            if history:
                raise SchemaError("revision 0 must have empty confirmed history")
            return
        if not history:
            raise SchemaError("revision > 0 requires confirmed history")
        last = history[-1]
        if last.revision != game.score_revision:
            raise SchemaError("current revision must equal newest event revision")
        if last.new_pair != game.pair:
            raise SchemaError("current trusted score must equal newest confirmed event")

    def _load_revision(
        self, game_id: UUID, revision: int
    ) -> ConfirmedScoreChange | None:
        row = self._conn.execute(
            """
            SELECT revision, previous_left, previous_right, new_left, new_right,
                   initialization, evidence_count, first_seen_timestamp_s,
                   first_seen_frame_index, confirmed_timestamp_s,
                   confirmed_frame_index, confirmed_epoch
              FROM confirmed_score_changes
             WHERE game_id = %s AND revision = %s
            """,
            (game_id, revision),
        ).fetchone()
        return None if row is None else _change_from_row(row)

    @_serialized
    def list_matchup_changes(self, game_id: UUID) -> tuple[ConfirmedMatchupChange, ...]:
        rows = self._conn.execute(
            """
            SELECT revision, left_team_id, right_team_id, left_method, right_method,
                   evidence_count, first_seen_timestamp_s, first_seen_frame_index,
                   confirmed_timestamp_s, confirmed_frame_index, confirmed_epoch
              FROM confirmed_matchup_changes
             WHERE game_id = %s
             ORDER BY revision
            """,
            (game_id,),
        ).fetchall()
        return tuple(_matchup_from_row(row) for row in rows)

    @_serialized
    def persist_matchup_confirmation(
        self, game_id: UUID, change: ConfirmedMatchupChange
    ) -> MatchupPersistResult:
        """Append matchup history and set current confirmed pair. Never rewrites."""
        if change.left_team_id == change.right_team_id:
            raise ConflictingMatchupError("left and right team ids must differ")
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                    (game_id,),
                ).fetchone()
                if row is None:
                    raise UnknownGameError(f"game {game_id} not found")
                current = _game_from_row(row)
                if change.revision == current.matchup_revision:
                    stored = self._load_matchup_revision(game_id, change.revision)
                    if stored is not None and matchup_payload_matches(stored, change):
                        return MatchupPersistResult(
                            PersistOutcome.IDEMPOTENT, current, stored
                        )
                    raise ConflictingMatchupError(
                        f"matchup revision {change.revision} already persisted "
                        "with different data"
                    )
                if current.matchup_revision > 0:
                    raise ConflictingMatchupError(
                        "confirmed matchup cannot be rewritten; "
                        f"persisted {current.confirmed_left_team_id}/"
                        f"{current.confirmed_right_team_id} revision "
                        f"{current.matchup_revision}"
                    )
                if change.revision != 1:
                    raise ConflictingMatchupError(
                        f"expected matchup revision 1, got {change.revision}"
                    )
                result = self._insert_and_update_matchup(game_id, change)
            emit_game_change(self._publisher, KIND_MATCHUP_CONFIRMED, result.game)
            return result
        except UniqueViolation as exc:
            stored = self._load_matchup_revision(game_id, change.revision)
            if stored is not None and matchup_payload_matches(stored, change):
                return MatchupPersistResult(
                    PersistOutcome.IDEMPOTENT, self.get_game(game_id), stored
                )
            raise ConflictingMatchupError(
                f"matchup revision {change.revision} unique conflict"
            ) from exc

    def _insert_and_update_matchup(
        self, game_id: UUID, change: ConfirmedMatchupChange
    ) -> MatchupPersistResult:
        self._conn.execute(
            """
            INSERT INTO confirmed_matchup_changes (
                id, game_id, revision, left_team_id, right_team_id,
                left_method, right_method, evidence_count,
                first_seen_timestamp_s, first_seen_frame_index,
                confirmed_timestamp_s, confirmed_frame_index,
                confirmed_epoch, persisted_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                uuid4(),
                game_id,
                change.revision,
                change.left_team_id,
                change.right_team_id,
                change.left_method.value,
                change.right_method.value,
                change.evidence_count,
                change.first_seen_timestamp_s,
                change.first_seen_frame_index,
                change.confirmed_timestamp_s,
                change.confirmed_frame_index,
                change.confirmed_epoch,
                datetime.now(tz=UTC),
            ),
        )
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE games SET
                confirmed_left_team_id = %s,
                confirmed_right_team_id = %s,
                matchup_revision = %s,
                matchup_confirmed_at = %s,
                matchup_confirmed_timestamp_s = %s,
                matchup_confirmed_frame_index = %s,
                matchup_confirmed_epoch = %s,
                matchup_left_method = %s,
                matchup_right_method = %s,
                updated_at = %s
             WHERE id = %s
            """,
            (
                change.left_team_id,
                change.right_team_id,
                change.revision,
                now,
                change.confirmed_timestamp_s,
                change.confirmed_frame_index,
                change.confirmed_epoch,
                change.left_method.value,
                change.right_method.value,
                now,
                game_id,
            ),
        )
        game = self.get_game(game_id)
        return MatchupPersistResult(PersistOutcome.APPLIED, game, change)

    def _load_matchup_revision(
        self, game_id: UUID, revision: int
    ) -> ConfirmedMatchupChange | None:
        row = self._conn.execute(
            """
            SELECT revision, left_team_id, right_team_id, left_method, right_method,
                   evidence_count, first_seen_timestamp_s, first_seen_frame_index,
                   confirmed_timestamp_s, confirmed_frame_index, confirmed_epoch
              FROM confirmed_matchup_changes
             WHERE game_id = %s AND revision = %s
            """,
            (game_id, revision),
        ).fetchone()
        return None if row is None else _matchup_from_row(row)

    @_serialized
    def record_matchup_conflict(
        self, game_id: UUID, conflict_pair: tuple[str, str]
    ) -> Game:
        now = datetime.now(tz=UTC)
        label = f"{conflict_pair[0]}/{conflict_pair[1]}"
        with self._conn.transaction():
            row = self._conn.execute(
                f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s FOR UPDATE",
                (game_id,),
            ).fetchone()
            if row is None:
                raise UnknownGameError(f"game {game_id} not found")
            self._conn.execute(
                """
                UPDATE games SET
                    matchup_conflict_count = matchup_conflict_count + 1,
                    last_matchup_conflict = %s,
                    updated_at = %s
                 WHERE id = %s
                """,
                (label, now, game_id),
            )
        game = self.get_game(game_id)
        emit_game_change(self._publisher, KIND_MATCHUP_CONFLICT, game)
        return game


def _matchup_from_row(row: dict_row | dict) -> ConfirmedMatchupChange:
    return ConfirmedMatchupChange(
        revision=int(row["revision"]),
        left_team_id=row["left_team_id"],
        right_team_id=row["right_team_id"],
        left_method=IdentityMethod(row["left_method"]),
        right_method=IdentityMethod(row["right_method"]),
        evidence_count=int(row["evidence_count"]),
        first_seen_timestamp_s=row["first_seen_timestamp_s"],
        first_seen_frame_index=row["first_seen_frame_index"],
        confirmed_timestamp_s=row["confirmed_timestamp_s"],
        confirmed_frame_index=row["confirmed_frame_index"],
        confirmed_epoch=row["confirmed_epoch"],
        initialization=int(row["revision"]) == 1,
    )


def _game_from_row(row: dict_row | dict) -> Game:
    tracking_enabled = row.get("tracking_enabled")
    if tracking_enabled is None:
        tracking_enabled = True
    lifecycle_raw = row.get("platform_lifecycle") or PlatformLifecycle.UNKNOWN.value
    return Game(
        id=row["id"],
        platform=row["platform"],
        external_broadcast_id=row["external_broadcast_id"],
        channel_id=row["channel_id"],
        tracking_status=TrackingStatus(row["tracking_status"]),
        trusted_left_score=row["trusted_left_score"],
        trusted_right_score=row["trusted_right_score"],
        score_revision=int(row["score_revision"]),
        last_confirmed_at=row["last_confirmed_at"],
        last_confirmed_timestamp_s=row["last_confirmed_timestamp_s"],
        last_confirmed_frame_index=row["last_confirmed_frame_index"],
        last_confirmed_epoch=row["last_confirmed_epoch"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        title=row.get("title"),
        platform_lifecycle=PlatformLifecycle(lifecycle_raw),
        scheduled_start_at=row.get("scheduled_start_at"),
        actual_start_at=row.get("actual_start_at"),
        actual_end_at=row.get("actual_end_at"),
        last_synced_at=row.get("last_synced_at"),
        tracking_enabled=bool(tracking_enabled),
        last_tracking_error=row.get("last_tracking_error"),
        last_worker_finished_at=row.get("last_worker_finished_at"),
        confirmed_left_team_id=row.get("confirmed_left_team_id"),
        confirmed_right_team_id=row.get("confirmed_right_team_id"),
        matchup_revision=int(row.get("matchup_revision") or 0),
        matchup_confirmed_at=row.get("matchup_confirmed_at"),
        matchup_confirmed_timestamp_s=row.get("matchup_confirmed_timestamp_s"),
        matchup_confirmed_frame_index=row.get("matchup_confirmed_frame_index"),
        matchup_confirmed_epoch=row.get("matchup_confirmed_epoch"),
        matchup_left_method=row.get("matchup_left_method"),
        matchup_right_method=row.get("matchup_right_method"),
        matchup_conflict_count=int(row.get("matchup_conflict_count") or 0),
        last_matchup_conflict=row.get("last_matchup_conflict"),
    )


def _change_from_row(row: dict_row | dict) -> ConfirmedScoreChange:
    return ConfirmedScoreChange(
        revision=int(row["revision"]),
        previous_left=row["previous_left"],
        previous_right=row["previous_right"],
        new_left=int(row["new_left"]),
        new_right=int(row["new_right"]),
        initialization=bool(row["initialization"]),
        evidence_count=int(row["evidence_count"]),
        first_seen_timestamp_s=row["first_seen_timestamp_s"],
        first_seen_frame_index=row["first_seen_frame_index"],
        confirmed_timestamp_s=row["confirmed_timestamp_s"],
        confirmed_frame_index=row["confirmed_frame_index"],
        confirmed_epoch=row["confirmed_epoch"],
    )
