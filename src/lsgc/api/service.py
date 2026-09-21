"""Read-oriented product queries over the persistence repository."""

from __future__ import annotations

from uuid import UUID

from lsgc.api.mapping import game_summary, timeline_event
from lsgc.api.schemas import GameListResponse, GameSummary, ScoreTimelineResponse
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
