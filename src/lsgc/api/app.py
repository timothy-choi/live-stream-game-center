"""FastAPI application factory.

API process + optional M9 orchestrator in one process. Tracking remains
backend-owned. Tests inject a store and never need YouTube credentials.
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lsgc.api.config import ApiSettings, load_api_settings
from lsgc.api.hub import GameEventHub
from lsgc.api.routes import register_exception_handlers, router
from lsgc.identity.catalog import DEFAULT_CATALOG, TeamCatalog
from lsgc.persist.config import database_url as load_database_url
from lsgc.persist.migrate import apply_migrations, connect
from lsgc.persist.pool import PooledGameRepository, open_pool

_UNSET = object()
_OAUTH_QUERY_RE = re.compile(
    r"(?i)\b(code|state|scope|iss)=([^&\s]+)"
)


class _RedactOAuthAccessLogFilter(logging.Filter):
    """Keep uvicorn access logs from recording OAuth codes or state."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if "oauth" not in message.lower() and "code=" not in message:
            return True
        record.msg = _OAUTH_QUERY_RE.sub(r"\1=[redacted]", message)
        record.args = ()
        return True


def _install_oauth_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, _RedactOAuthAccessLogFilter) for item in logger.filters):
        return
    logger.addFilter(_RedactOAuthAccessLogFilter())


_install_oauth_access_log_redaction()


def create_app(
    *,
    store: Any | None = None,
    hub: GameEventHub | None = None,
    settings: ApiSettings | None = None,
    catalog: TeamCatalog | None = None,
    orchestrator: Any | None = None,
    connection_service: Any = _UNSET,
    calibration: Any | None = None,
) -> FastAPI:
    """Build the API. Inject `store` in tests. Production uses a connection pool."""
    resolved_settings = settings or load_api_settings()
    resolved_catalog = catalog or DEFAULT_CATALOG
    resolved_hub = hub or GameEventHub(
        catalog=resolved_catalog, max_queue=resolved_settings.max_queue
    )
    owned_pool = None
    owned_store = store
    if owned_store is None:
        dsn = load_database_url()
        with connect(dsn) as conn:
            apply_migrations(conn)
            conn.commit()
        owned_pool = open_pool(dsn)
        owned_store = PooledGameRepository(owned_pool, publisher=resolved_hub)

    if calibration is None:
        from lsgc.broadcast.calibration import FilesystemCalibrationLookup
        from lsgc.broadcast.config import load_runtime_config

        try:
            calibration = FilesystemCalibrationLookup(load_runtime_config().calibration_dir)
        except Exception:
            from lsgc.broadcast.calibration import MappingCalibrationLookup

            calibration = MappingCalibrationLookup()
    if connection_service is _UNSET:
        from lsgc.youtube_oauth.service import maybe_connection_service

        connection_service = maybe_connection_service(owned_store, calibration)

    stop = threading.Event()
    orchestrator_thread: threading.Thread | None = None
    started_orchestrator = orchestrator

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal started_orchestrator, orchestrator_thread
        if resolved_settings.run_orchestrator and started_orchestrator is None:
            started_orchestrator = _start_orchestrator(
                owned_store,
                connection_service=connection_service,
                calibration=calibration,
            )
        if started_orchestrator is not None and hasattr(started_orchestrator, "run_forever"):
            orchestrator_thread = threading.Thread(
                target=started_orchestrator.run_forever,
                kwargs={"stop": stop},
                name="lsgc-orchestrator",
                daemon=True,
            )
            orchestrator_thread.start()
        app.state.orchestrator = started_orchestrator
        yield
        stop.set()
        if started_orchestrator is not None and hasattr(started_orchestrator, "shutdown"):
            started_orchestrator.shutdown()
        if orchestrator_thread is not None:
            orchestrator_thread.join(timeout=5)
        if owned_pool is not None:
            owned_pool.close()

    app = FastAPI(
        title="Live Stream Game Center API",
        description=(
            "Read-only product API for YouTube game broadcasts. "
            "Tracking is backend-owned; API clients never start or stop it. "
            "M11 has no authentication — local/development trust only."
        ),
        version="0.11.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.catalog = resolved_catalog
    app.state.hub = resolved_hub
    app.state.store = owned_store
    app.state.orchestrator = started_orchestrator
    app.state.connection_service = connection_service
    app.state.calibration = calibration
    if resolved_settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
            allow_headers=["*"],
        )
    register_exception_handlers(app)
    app.include_router(router)
    return app


def _start_orchestrator(store: Any, *, connection_service, calibration):
    from lsgc.broadcast.config import load_runtime_config
    from lsgc.broadcast.orchestrator import orchestrator_from_config
    from lsgc.youtube_oauth.catalog import HybridBroadcastCatalog, merge_sync_channels

    config = load_runtime_config()
    catalog = HybridBroadcastCatalog(
        store=store,
        service=connection_service,
        developer_api_key=config.api_key,
        developer_access_token=config.access_token,
    )
    return orchestrator_from_config(
        config,
        store=store,
        catalog=catalog,
        calibration=calibration,
        channel_provider=lambda: merge_sync_channels(store, config.channels),
    )


def app_from_env() -> FastAPI:
    return create_app()
