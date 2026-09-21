"""Apply migrations using DATABASE_URL.

    python -m lsgc.persist migrate
"""

from __future__ import annotations

import argparse
import sys

from lsgc.persist.config import database_url
from lsgc.persist.errors import PersistError
from lsgc.persist.migrate import apply_migrations, applied_versions, connect

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.persist",
        description="Durable trusted-score persistence (no REST/API).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply versioned SQL migrations")
    return parser


def _migrate() -> int:
    dsn = database_url()
    with connect(dsn) as conn:
        applied = apply_migrations(conn)
        versions = applied_versions(conn)
        if applied:
            print(f"applied {applied}; schema now {versions}", flush=True)
        else:
            print(f"already current; schema {versions}", flush=True)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "migrate":
            return _migrate()
    except PersistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
