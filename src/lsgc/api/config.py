"""API process configuration from the environment.

No hard-coded PostgreSQL credentials. CORS is opt-in by origin list.
M11 is a local/development trust boundary: the API is read-only and has
no authentication.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False, *, environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, environ: dict[str, str] | None = None) -> float:
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    return float(raw)


def _env_int(name: str, default: int, *, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class ApiSettings:
    cors_origins: tuple[str, ...]
    run_orchestrator: bool
    heartbeat_s: float
    max_queue: int
    default_limit: int = 50
    max_limit: int = 100

    @property
    def cors_enabled(self) -> bool:
        return bool(self.cors_origins)


def load_api_settings(*, environ: dict[str, str] | None = None) -> ApiSettings:
    env = os.environ if environ is None else environ
    raw_origins = (env.get("LSGC_CORS_ORIGINS") or "").strip()
    origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())
    heartbeat = _env_float("LSGC_SSE_HEARTBEAT_S", 15.0, environ=env)
    if heartbeat <= 0:
        raise ValueError("LSGC_SSE_HEARTBEAT_S must be > 0")
    max_queue = _env_int("LSGC_SSE_MAX_QUEUE", 32, environ=env)
    if max_queue < 1:
        raise ValueError("LSGC_SSE_MAX_QUEUE must be >= 1")
    return ApiSettings(
        cors_origins=origins,
        run_orchestrator=_env_bool("LSGC_RUN_ORCHESTRATOR", False, environ=env),
        heartbeat_s=heartbeat,
        max_queue=max_queue,
    )
