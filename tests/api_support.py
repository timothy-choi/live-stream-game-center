"""Shared builders for M11 API tests. No YouTube."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from lsgc.api.app import create_app
from lsgc.api.config import ApiSettings
from lsgc.api.hub import GameEventHub
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.identity.models import ConfirmedMatchupChange, IdentityMethod
from lsgc.persist.models import PLATFORM_YOUTUBE, PlatformLifecycle
from lsgc.track.models import ConfirmedScoreChange
from persist_memory import InMemoryGameRepository


CHANNEL = "UCapiChannelTest000000000000"


class FakeOrchestrator:
    def __init__(self) -> None:
        self.ensure_calls: list[object] = []

    def ensure_tracking(self, game) -> None:
        self.ensure_calls.append(getattr(game, "id", game))


def score_change(
    revision: int,
    left: int,
    right: int,
    previous_left: int | None = None,
    previous_right: int | None = None,
) -> ConfirmedScoreChange:
    return ConfirmedScoreChange(
        revision=revision,
        previous_left=previous_left,
        previous_right=previous_right,
        new_left=left,
        new_right=right,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=float(revision * 2),
        confirmed_frame_index=revision,
        confirmed_epoch=1,
        initialization=revision == 1,
    )


def matchup_change(
    left: str = "SF",
    right: str = "NYG",
    revision: int = 1,
) -> ConfirmedMatchupChange:
    return ConfirmedMatchupChange(
        revision=revision,
        left_team_id=left,
        right_team_id=right,
        left_method=IdentityMethod.LOGO,
        right_method=IdentityMethod.LOGO,
        evidence_count=2,
        first_seen_timestamp_s=0.0,
        first_seen_frame_index=0,
        confirmed_timestamp_s=2.0,
        confirmed_frame_index=1,
        confirmed_epoch=1,
        initialization=revision == 1,
    )


def upsert(
    store,
    broadcast_id: str,
    lifecycle: PlatformLifecycle,
    *,
    channel_id: str = CHANNEL,
    title: str | None = None,
    hours: float = 1.0,
) -> object:
    now = datetime.now(tz=UTC)
    scheduled = now + timedelta(hours=hours)
    actual_start = now if lifecycle is PlatformLifecycle.LIVE else None
    actual_end = now if lifecycle is PlatformLifecycle.ENDED else None
    if lifecycle is PlatformLifecycle.ENDED:
        scheduled = now - timedelta(hours=2)
        actual_start = now - timedelta(hours=2)
    return store.upsert_broadcast_metadata(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id=broadcast_id,
        channel_id=channel_id,
        title=title or broadcast_id,
        platform_lifecycle=lifecycle,
        scheduled_start_at=scheduled,
        actual_start_at=actual_start,
        actual_end_at=actual_end,
        synced_at=now,
    )


def make_settings(*, heartbeat_s: float = 0.05, max_queue: int = 32) -> ApiSettings:
    return ApiSettings(
        cors_origins=("http://localhost:5173",),
        run_orchestrator=False,
        heartbeat_s=heartbeat_s,
        max_queue=max_queue,
    )


def make_client(store, hub: GameEventHub | None = None, **kwargs) -> TestClient:
    resolved_hub = hub or GameEventHub(DEFAULT_CATALOG, max_queue=kwargs.get("max_queue", 32))
    settings = make_settings(
        heartbeat_s=kwargs.get("heartbeat_s", 0.05),
        max_queue=kwargs.get("max_queue", 32),
    )
    orchestrator = kwargs.get("orchestrator", FakeOrchestrator())
    app = create_app(
        store=store,
        hub=resolved_hub,
        settings=settings,
        catalog=DEFAULT_CATALOG,
        orchestrator=orchestrator,
    )
    return TestClient(app)


def parse_sse_events(response, *, min_events: int = 1, limit: int = 20):
    events = []
    heartbeats = 0
    event_name = None
    data_lines: list[str] = []
    for raw in response.iter_lines():
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if line.startswith(":"):
            heartbeats += 1
            continue
        if line == "":
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                events.append((event_name or payload.get("type"), payload))
                event_name = None
                data_lines = []
                if len(events) >= min_events:
                    break
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        if len(events) + heartbeats >= limit:
            break
    return events, heartbeats


def parse_sse_text(body: str):
    events = []
    heartbeats = 0
    event_name = None
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith(":"):
            heartbeats += 1
            continue
        if line == "":
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                events.append((event_name or payload.get("type"), payload))
                event_name = None
                data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        payload = json.loads("\n".join(data_lines))
        events.append((event_name or payload.get("type"), payload))
    return events, heartbeats


def collect_live_sse(app, game_id, *, min_events: int = 2, mutate=None):
    """Drive the ASGI SSE generator without TestClient stream buffering."""
    from starlette.requests import Request
    from lsgc.api.routes import _event_stream

    async def _run():
        scope = {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/games/{game_id}/events",
            "raw_path": f"/api/games/{game_id}/events".encode(),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive)
        subscriber = app.state.hub.subscribe(game_id)
        body = ""
        loop = asyncio.get_running_loop()
        if mutate is not None:

            def _mutate_safe() -> None:
                time.sleep(0.05)
                mutate()

            loop.run_in_executor(None, _mutate_safe)
        deadline = loop.time() + 4.0
        async for chunk in _event_stream(request, game_id, subscriber):
            body += chunk
            events, heartbeats = parse_sse_text(body)
            if min_events <= 0:
                if heartbeats >= 1:
                    break
                continue
            if len(events) >= min_events:
                break
            if loop.time() >= deadline:
                break
        return parse_sse_text(body)

    return asyncio.run(_run())
