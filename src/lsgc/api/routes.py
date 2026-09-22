"""HTTP routes. Read-only. Never start tracking workers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lsgc.api.hub import GameEventHub, Subscriber
from lsgc.api.schemas import (
    ErrorBody,
    GameEvent,
    GameListResponse,
    GameSummary,
    HealthResponse,
    OrchestratorDiagnostic,
    ScoreTimelineResponse,
    YoutubeConnectionListResponse,
)
from lsgc.api.service import (
    database_status,
    list_games,
    list_score_events,
    list_youtube_connections,
    orchestrator_diagnostic,
    read_game,
)
from lsgc.youtube_oauth.errors import OAuthNotConfiguredError, YoutubeOAuthError
from lsgc.persist.errors import PersistError, UnknownGameError

router = APIRouter()


def _store(request: Request):
    return request.app.state.store


def _catalog(request: Request):
    return request.app.state.catalog


def _hub(request: Request) -> GameEventHub:
    return request.app.state.hub


def _settings(request: Request):
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    db = database_status(_store(request))
    status = "ok" if db == "ok" else "degraded"
    return HealthResponse(status=status, process="alive", database=db)


@router.get("/api/diagnostics/orchestrator", response_model=OrchestratorDiagnostic)
def get_orchestrator_diagnostic(request: Request) -> OrchestratorDiagnostic:
    """Read-only M9 loop status. Does not start, stop, or own tracking."""
    return orchestrator_diagnostic(getattr(request.app.state, "orchestrator", None))


@router.get("/api/games", response_model=GameListResponse)
def get_games(
    request: Request,
    channel_id: str | None = Query(default=None),
    lifecycle: str | None = Query(
        default=None,
        description="Comma-separated platform lifecycle values, e.g. LIVE,UPCOMING",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> GameListResponse:
    return list_games(
        _store(request),
        _catalog(request),
        channel_id=channel_id,
        lifecycle=lifecycle,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/api/games/{game_id}",
    response_model=GameSummary,
    responses={404: {"model": ErrorBody}},
)
def get_game(game_id: UUID, request: Request) -> GameSummary:
    return read_game(_store(request), _catalog(request), game_id)


@router.get(
    "/api/games/{game_id}/score-events",
    response_model=ScoreTimelineResponse,
    responses={404: {"model": ErrorBody}},
)
def get_score_events(
    game_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ScoreTimelineResponse:
    return list_score_events(_store(request), game_id, limit=limit, offset=offset)


def _sse_encode(event: GameEvent) -> str:
    payload = event.model_dump_json()
    return f"id: {event.sequence}\nevent: {event.type.value}\ndata: {payload}\n\n"


async def _event_stream(
    request: Request,
    game_id: UUID,
    subscriber: Subscriber,
    *,
    once: bool = False,
) -> AsyncIterator[str]:
    hub = _hub(request)
    settings = _settings(request)
    heartbeat = settings.heartbeat_s
    try:
        game = _store(request).get_game(game_id)
        snapshot = hub.snapshot_event(game)
        yield _sse_encode(snapshot)
        if once:
            return
        while True:
            if await request.is_disconnected():
                break
            if subscriber.overflowed:
                yield _sse_encode(hub.resync_event(game_id))
                break
            event = await asyncio.to_thread(hub.wait, subscriber, heartbeat)
            if subscriber.overflowed:
                yield _sse_encode(hub.resync_event(game_id))
                break
            if event is None:
                yield ": ping\n\n"
                continue
            yield _sse_encode(event)
    finally:
        hub.unsubscribe(subscriber)


@router.get(
    "/api/games/{game_id}/events",
    responses={
        200: {
            "description": "text/event-stream of GameEvent envelopes",
            "model": GameEvent,
        },
        404: {"model": ErrorBody},
    },
)
async def game_events(
    game_id: UUID,
    request: Request,
    once: bool = Query(
        default=False,
        description="If true, send GAME_SNAPSHOT and close. Reconnect-safe catch-up.",
    ),
) -> StreamingResponse:
    """SSE product events. Subscribing does not start tracking."""
    _store(request).get_game(game_id)
    subscriber = _hub(request).subscribe(game_id)
    return StreamingResponse(
        _event_stream(request, game_id, subscriber, once=once),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _connections(request: Request):
    service = getattr(request.app.state, "connection_service", None)
    if service is None:
        raise OAuthNotConfiguredError("YouTube connection is not configured")
    return service


def _oauth_error_redirect(request: Request, code: str) -> RedirectResponse:
    service = getattr(request.app.state, "connection_service", None)
    if service is not None:
        return RedirectResponse(service.frontend_redirect(error=code), status_code=302)
    from lsgc.youtube_oauth.config import load_google_oauth_settings

    origin = load_google_oauth_settings().web_origin.rstrip("/")
    return RedirectResponse(f"{origin}/youtube/connected?error={code}", status_code=302)


@router.get("/api/youtube/oauth/start")
def youtube_oauth_start(request: Request) -> RedirectResponse:
    """Redirect the browser to Google. Server owns scopes, state, and redirect."""
    service = getattr(request.app.state, "connection_service", None)
    if service is None:
        return _oauth_error_redirect(request, "not_configured")
    url = service.start_authorization()
    return RedirectResponse(url, status_code=302)


@router.get("/api/youtube/oauth/callback")
def youtube_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    service = getattr(request.app.state, "connection_service", None)
    if service is None:
        return _oauth_error_redirect(request, "not_configured")
    try:
        service.complete_authorization(code=code, state=state, error=error)
    except Exception as exc:
        return RedirectResponse(
            service.frontend_redirect(error=service.public_error_code(exc)),
            status_code=302,
        )
    return RedirectResponse(service.frontend_redirect(), status_code=302)


@router.get("/api/youtube/connections", response_model=YoutubeConnectionListResponse)
def get_youtube_connections(request: Request) -> YoutubeConnectionListResponse:
    service = getattr(request.app.state, "connection_service", None)
    if service is None:
        return YoutubeConnectionListResponse(connections=[])
    return list_youtube_connections(service)


@router.post(
    "/api/youtube/connections/{connection_id}/disconnect",
    response_model=YoutubeConnectionListResponse,
)
def disconnect_youtube_connection(
    connection_id: UUID, request: Request
) -> YoutubeConnectionListResponse:
    service = _connections(request)
    service.disconnect(connection_id)
    return list_youtube_connections(service)


def register_exception_handlers(app) -> None:
    @app.exception_handler(UnknownGameError)
    async def _unknown_game(_request: Request, _exc: UnknownGameError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "game not found"})

    @app.exception_handler(PersistError)
    async def _persist(_request: Request, exc: PersistError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(OAuthNotConfiguredError)
    async def _oauth_unconfigured(
        _request: Request, exc: OAuthNotConfiguredError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(YoutubeOAuthError)
    async def _oauth(_request: Request, exc: YoutubeOAuthError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
            raise exc
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
