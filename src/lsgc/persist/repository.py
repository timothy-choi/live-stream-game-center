"""PostgreSQL repository for durable games and confirmed score history.

M7 validator and M4/M6 contain no SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from lsgc.persist.errors import (
    ConflictingConfirmationError,
    PersistError,
    SchemaError,
    StaleConfirmationError,
    UnknownGameError,
)
from lsgc.persist.models import Game, PersistOutcome, PersistResult, TrackingStatus
from lsgc.track.models import ConfirmedScoreChange

_GAME_COLUMNS = """
    id, platform, external_broadcast_id, channel_id, tracking_status,
    trusted_left_score, trusted_right_score, score_revision,
    last_confirmed_at, last_confirmed_timestamp_s,
    last_confirmed_frame_index, last_confirmed_epoch,
    created_at, updated_at
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


class PostgresGameRepository:
    """SQL for games + confirmed_score_changes. One connection, caller-owned."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

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

    def get_game(self, game_id: UUID) -> Game:
        row = self._conn.execute(
            f"SELECT {_GAME_COLUMNS} FROM games WHERE id = %s",
            (game_id,),
        ).fetchone()
        if row is None:
            raise UnknownGameError(f"game {game_id} not found")
        return _game_from_row(row)

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
                return self._insert_and_update(game_id, change)
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
            return self.get_game(game_id)

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


def _game_from_row(row: dict_row | dict) -> Game:
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
