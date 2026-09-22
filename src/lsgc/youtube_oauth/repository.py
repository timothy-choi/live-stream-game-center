"""PostgreSQL storage for YouTube connections and OAuth state."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation

from lsgc.persist.errors import PersistError, UnknownGameError
from lsgc.youtube_oauth.errors import ConnectionPersistError
from lsgc.youtube_oauth.models import (
    ConnectionStatus,
    OAuthStateRecord,
    YoutubeConnection,
)

_COLUMNS = """
    id, provider, youtube_channel_id, youtube_channel_title, status,
    granted_scopes, refresh_token_ciphertext, access_token_ciphertext,
    access_token_expires_at, token_type, connected_at, created_at, updated_at,
    last_refreshed_at, last_used_at, disconnected_at, owner_user_id
"""


def _row(row: dict) -> YoutubeConnection:
    return YoutubeConnection(
        id=row["id"],
        provider=row["provider"],
        youtube_channel_id=row["youtube_channel_id"],
        youtube_channel_title=row["youtube_channel_title"],
        status=ConnectionStatus(row["status"]),
        granted_scopes=row["granted_scopes"],
        connected_at=row["connected_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_refreshed_at=row["last_refreshed_at"],
        last_used_at=row["last_used_at"],
        disconnected_at=row["disconnected_at"],
        owner_user_id=row["owner_user_id"],
        refresh_token_ciphertext=row["refresh_token_ciphertext"],
        access_token_ciphertext=row["access_token_ciphertext"],
        access_token_expires_at=row["access_token_expires_at"],
        token_type=row["token_type"],
    )


class PostgresYoutubeConnectionRepository:
    def __init__(self, conn) -> None:
        self._conn = conn

    def list_connections(self) -> tuple[YoutubeConnection, ...]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM youtube_connections "
            "WHERE status <> 'DISCONNECTED' ORDER BY connected_at DESC"
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def list_active_youtube_connections(self) -> tuple[YoutubeConnection, ...]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM youtube_connections "
            "WHERE status = 'CONNECTED' ORDER BY connected_at DESC"
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def get_connection(self, connection_id: UUID) -> YoutubeConnection:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM youtube_connections WHERE id = %s",
            (connection_id,),
        ).fetchone()
        if row is None:
            raise UnknownGameError("connection not found")
        return _row(row)

    def get_active_connection_for_channel(self, channel_id: str) -> YoutubeConnection | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM youtube_connections "
            "WHERE youtube_channel_id = %s AND status <> 'DISCONNECTED'",
            (channel_id,),
        ).fetchone()
        return _row(row) if row else None

    def upsert_connected_channel(
        self,
        *,
        youtube_channel_id: str,
        youtube_channel_title: str | None,
        granted_scopes: str | None,
        refresh_token_ciphertext: str,
        access_token_ciphertext: str | None,
        access_token_expires_at: datetime | None,
        token_type: str | None,
    ) -> YoutubeConnection:
        now = datetime.now(tz=UTC)
        existing = self.get_active_connection_for_channel(youtube_channel_id)
        if existing is not None:
            self._conn.execute(
                """
                UPDATE youtube_connections SET
                    youtube_channel_title = %s,
                    status = 'CONNECTED',
                    granted_scopes = %s,
                    refresh_token_ciphertext = %s,
                    access_token_ciphertext = %s,
                    access_token_expires_at = %s,
                    token_type = %s,
                    connected_at = %s,
                    updated_at = %s,
                    last_refreshed_at = %s,
                    disconnected_at = NULL
                WHERE id = %s
                """,
                (
                    youtube_channel_title,
                    granted_scopes,
                    refresh_token_ciphertext,
                    access_token_ciphertext,
                    access_token_expires_at,
                    token_type,
                    now,
                    now,
                    now,
                    existing.id,
                ),
            )
            return self.get_connection(existing.id)
        connection_id = uuid4()
        try:
            self._conn.execute(
                f"""
                INSERT INTO youtube_connections ({_COLUMNS})
                VALUES (
                    %s, 'youtube', %s, %s, 'CONNECTED', %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, NULL, NULL, NULL
                )
                """,
                (
                    connection_id,
                    youtube_channel_id,
                    youtube_channel_title,
                    granted_scopes,
                    refresh_token_ciphertext,
                    access_token_ciphertext,
                    access_token_expires_at,
                    token_type,
                    now,
                    now,
                    now,
                    now,
                ),
            )
        except UniqueViolation as exc:
            raise ConnectionPersistError("active connection already exists") from exc
        return self.get_connection(connection_id)

    def update_tokens(
        self,
        connection_id: UUID,
        *,
        refresh_token_ciphertext: str,
        access_token_ciphertext: str | None,
        access_token_expires_at: datetime | None,
        token_type: str | None,
        granted_scopes: str | None,
    ) -> YoutubeConnection:
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE youtube_connections SET
                refresh_token_ciphertext = %s,
                access_token_ciphertext = %s,
                access_token_expires_at = %s,
                token_type = %s,
                granted_scopes = COALESCE(%s, granted_scopes),
                last_refreshed_at = %s,
                last_used_at = %s,
                updated_at = %s,
                status = 'CONNECTED'
            WHERE id = %s AND status <> 'DISCONNECTED'
            """,
            (
                refresh_token_ciphertext,
                access_token_ciphertext,
                access_token_expires_at,
                token_type,
                granted_scopes,
                now,
                now,
                now,
                connection_id,
            ),
        )
        return self.get_connection(connection_id)

    def mark_needs_reauthorization(self, connection_id: UUID) -> YoutubeConnection:
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE youtube_connections SET
                status = 'NEEDS_REAUTHORIZATION',
                refresh_token_ciphertext = NULL,
                access_token_ciphertext = NULL,
                access_token_expires_at = NULL,
                updated_at = %s
            WHERE id = %s
            """,
            (now, connection_id),
        )
        return self.get_connection(connection_id)

    def mark_disconnected(self, connection_id: UUID) -> YoutubeConnection:
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            UPDATE youtube_connections SET
                status = 'DISCONNECTED',
                refresh_token_ciphertext = NULL,
                access_token_ciphertext = NULL,
                access_token_expires_at = NULL,
                disconnected_at = %s,
                updated_at = %s
            WHERE id = %s
            """,
            (now, now, connection_id),
        )
        return self.get_connection(connection_id)

    def create_oauth_state(self, state: str, *, expires_at: datetime) -> OAuthStateRecord:
        now = datetime.now(tz=UTC)
        self._conn.execute(
            """
            INSERT INTO youtube_oauth_states (state, created_at, expires_at, consumed_at)
            VALUES (%s, %s, %s, NULL)
            """,
            (state, now, expires_at),
        )
        return OAuthStateRecord(state=state, created_at=now, expires_at=expires_at)

    def consume_oauth_state(self, state: str, *, now: datetime | None = None) -> OAuthStateRecord:
        clock = now or datetime.now(tz=UTC)
        row = self._conn.execute(
            "SELECT state, created_at, expires_at, consumed_at "
            "FROM youtube_oauth_states WHERE state = %s",
            (state,),
        ).fetchone()
        if row is None:
            raise PersistError("unknown oauth state")
        record = OAuthStateRecord(
            state=row["state"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
        )
        if record.consumed_at is not None:
            raise PersistError("oauth state already used")
        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= clock:
            raise PersistError("oauth state expired")
        self._conn.execute(
            "UPDATE youtube_oauth_states SET consumed_at = %s WHERE state = %s",
            (clock, state),
        )
        return OAuthStateRecord(
            state=record.state,
            created_at=record.created_at,
            expires_at=record.expires_at,
            consumed_at=clock,
        )
