"""Database connection configuration.

Production code reads DATABASE_URL from the environment. There is no
hard-coded localhost password.
"""

from __future__ import annotations

import os

from lsgc.persist.errors import ConfigError

DATABASE_URL_ENV = "DATABASE_URL"
TEST_DATABASE_URL_ENV = "LSGC_TEST_DATABASE_URL"


def database_url(*, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    url = (env.get(DATABASE_URL_ENV) or "").strip()
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set. Example: postgresql://user:pass@127.0.0.1:54329/lsgc"
        )
    return url


def postgres_test_dsn(*, environ: dict[str, str] | None = None) -> str | None:
    """Return a test DSN, or None if integration tests should skip.

    Prefers LSGC_TEST_DATABASE_URL. DATABASE_URL is accepted only when the
    database name looks like a test database, so pytest cannot truncate a
    developer database by accident.
    """
    env = os.environ if environ is None else environ
    explicit = (env.get(TEST_DATABASE_URL_ENV) or "").strip()
    if explicit:
        return explicit
    url = (env.get(DATABASE_URL_ENV) or "").strip()
    if not url:
        return None
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if name.endswith("_test") or name == "test":
        return url
    return None
