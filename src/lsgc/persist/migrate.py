"""Versioned SQL migrations. Runtime never auto-creates tables ad hoc."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import ClientCursor
from psycopg.rows import dict_row

from lsgc.persist.errors import SchemaError

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def migrations_dir() -> Path:
    return MIGRATIONS_DIR


def apply_migrations(
    conn: psycopg.Connection,
    directory: Path | None = None,
) -> list[int]:
    """Apply pending numbered `NNN_name.sql` files. Returns applied versions."""
    path = directory or migrations_dir()
    if not path.is_dir():
        raise SchemaError(f"migrations directory missing: {path}")
    files = sorted(path.glob("*.sql"))
    if not files:
        raise SchemaError(f"no SQL migrations in {path}")

    with conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    applied: list[int] = []
    for file in files:
        version, name = _parse_name(file.name)
        already = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = %s",
            (version,),
        ).fetchone()
        if already is not None:
            continue
        sql = file.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (version, name),
            )
        applied.append(version)
    return applied


def applied_versions(conn: psycopg.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    return [int(row["version"]) for row in rows]


def _parse_name(filename: str) -> tuple[int, str]:
    stem = filename.removesuffix(".sql")
    prefix, _, rest = stem.partition("_")
    if not prefix.isdigit() or not rest:
        raise SchemaError(
            f"migration filename must be NNN_description.sql, got {filename!r}"
        )
    return int(prefix), rest


def connect(dsn: str) -> psycopg.Connection:
    # ClientCursor uses the simple query protocol so a migration file may
    # contain multiple statements.
    return psycopg.connect(
        dsn, row_factory=dict_row, cursor_factory=ClientCursor
    )
