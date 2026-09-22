"""Read-oriented product queries over the persistence repository."""

from __future__ import annotations

from uuid import UUID

from lsgc.api.mapping import game_summary, timeline_event, utc_iso
from lsgc.api.schemas import (
    GameListResponse,
    GameSummary,
    OrchestratorDiagnostic,
    ScoreTimelineResponse,
    WorkerDiagnostic,
    YoutubeConnectionListResponse,
    YoutubeConnectionSummary,
)
from lsgc.youtube_oauth.models import YoutubeConnection
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.identity.catalog import TeamCatalog
from lsgc.persist.errors import PersistError
from lsgc.persist.models import PlatformLifecycle


def _parse_lifecycles(raw: str | None) -> tuple[PlatformLifecycle, ...] | None:
    if raw is None or not raw.strip():
        return None
    values: list[PlatformLifecycle] = []
    for part in raw.split(","):
        token = part.strip().upper()
        if not token:
            continue
        try:
            values.append(PlatformLifecycle(token))
        except ValueError as exc:
            raise PersistError(f"invalid lifecycle filter: {token}") from exc
    return tuple(values) or None


def read_game(store, catalog: TeamCatalog, game_id: UUID) -> GameSummary:
    return game_summary(store.get_game(game_id), catalog)


def list_games(
    store,
    catalog: TeamCatalog,
    *,
    channel_id: str | None,
    lifecycle: str | None,
    limit: int,
    offset: int,
) -> GameListResponse:
    lifecycles = _parse_lifecycles(lifecycle)
    games, total = store.list_games(
        channel_id=channel_id or None,
        lifecycles=lifecycles,
        limit=limit,
        offset=offset,
    )
    return GameListResponse(
        games=[game_summary(game, catalog) for game in games],
        total=total,
        limit=limit,
        offset=offset,
    )


def list_score_events(
    store,
    game_id: UUID,
    *,
    limit: int,
    offset: int,
) -> ScoreTimelineResponse:
    store.get_game(game_id)
    records = store.list_score_change_records(game_id)
    total = len(records)
    window = records[offset : offset + limit]
    return ScoreTimelineResponse(
        game_id=game_id,
        events=[timeline_event(record) for record in window],
        total=total,
        limit=limit,
        offset=offset,
    )


def orchestrator_diagnostic(orchestrator) -> OrchestratorDiagnostic:
    """Read-only snapshot of the in-process M9 loop. Never starts a worker."""
    status = getattr(orchestrator, "status", None)
    if orchestrator is None or status is None:
        return OrchestratorDiagnostic(orchestrator_enabled=False)
    workers = []
    for item in getattr(status, "workers", ()) or ():
        workers.append(
            WorkerDiagnostic(
                broadcast_id=item.broadcast_id,
                state=item.state.value if hasattr(item.state, "value") else str(item.state),
                frames_emitted=int(getattr(item, "frames_emitted", 0) or 0),
                retry_count=int(getattr(item, "retry_count", 0) or 0),
                last_error=sanitize_control_plane_text(getattr(item, "last_error", None)),
                started_at=utc_iso(getattr(item, "started_at", None)),
                finished_at=utc_iso(getattr(item, "finished_at", None)),
                next_retry_at=utc_iso(getattr(item, "next_retry_at", None)),
            )
        )
    return OrchestratorDiagnostic(
        orchestrator_enabled=True,
        last_sync_at=utc_iso(status.last_sync_at),
        last_reconcile_at=utc_iso(status.last_reconcile_at),
        last_sync_ok=status.last_sync_ok,
        last_sync_error=sanitize_control_plane_text(status.last_sync_error),
        discovered_count=int(status.discovered_count),
        live_count=int(status.live_count),
        upcoming_count=int(status.upcoming_count),
        ended_count=int(status.ended_count),
        active_worker_count=int(status.active_worker_count),
        workers=workers,
    )


def connection_summary(connection: YoutubeConnection, calibration_status: str) -> YoutubeConnectionSummary:
    return YoutubeConnectionSummary(
        id=connection.id,
        provider=connection.provider,
        youtube_channel_id=connection.youtube_channel_id,
        youtube_channel_title=connection.youtube_channel_title,
        status=connection.status.value,
        calibration_status=calibration_status,
        connected_at=require_utc_iso_safe(connection.connected_at),
        last_refreshed_at=utc_iso(connection.last_refreshed_at),
    )


def require_utc_iso_safe(value) -> str:
    stamp = utc_iso(value)
    if stamp is None:
        raise PersistError("timestamp is required")
    return stamp


def list_youtube_connections(service) -> YoutubeConnectionListResponse:
    items = []
    for connection in service.list_connections():
        items.append(
            connection_summary(connection, service.calibration_for(connection).value)
        )
    return YoutubeConnectionListResponse(connections=items)


def database_status(store) -> str:
    if store is None:
        return "unconfigured"
    ping = getattr(store, "ping", None)
    if ping is None:
        return "unconfigured"
    try:
        ping()
    except Exception:
        return "unavailable"
    return "ok"
