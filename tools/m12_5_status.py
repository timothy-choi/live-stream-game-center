"""Read-only M12.5 diagnostic status.

    python tools/m12_5_status.py
    python tools/m12_5_status.py --discover
    python tools/m12_5_status.py --game-id UUID

Inspects durable API/Postgres state and optional YouTube Data API discovery.
Does not start, stop, or reset tracking. Does not seed games. Does not print
credentials, signed media URLs, or raw OCR evidence.

pytest never runs this.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("'").strip('"')


_load_env_file(ROOT / ".env")

from lsgc.broadcast.sanitize import sanitize_control_plane_text  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8000"
SECRET_KEYS = (
    "key",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "dsn",
    "database_url",
    "signed",
    "cdn",
    "media_url",
)


def _api_base() -> str:
    return (os.environ.get("LSGC_API_BASE_URL") or DEFAULT_API).rstrip("/")


def _assert_no_secrets(payload: Any, trail: str = "root") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SECRET_KEYS):
                raise RuntimeError(f"refusing to display secret-like field {trail}.{key}")
            _assert_no_secrets(value, f"{trail}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_secrets(item, f"{trail}[{index}]")


def _get_json(path: str) -> tuple[Any, int]:
    url = path if path.startswith("http") else urljoin(_api_base() + "/", path.lstrip("/"))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
            _assert_no_secrets(payload)
            return payload, response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            _assert_no_secrets(payload)
        except (json.JSONDecodeError, RuntimeError):
            payload = {"detail": sanitize_control_plane_text(raw[:200])}
        return payload, exc.code


def _print_game(game: dict[str, Any]) -> None:
    score = game.get("score") or {}
    matchup = game.get("matchup") or {}
    left = (matchup.get("left_team") or {}).get("id") or "null"
    right = (matchup.get("right_team") or {}).get("id") or "null"
    print(
        f"  id={game.get('id')} yt={game.get('external_broadcast_id')} "
        f"lifecycle={game.get('platform_lifecycle')} "
        f"tracking={game.get('tracking_status')} enabled={game.get('tracking_enabled')} "
        f"matchup={matchup.get('status')}/{left}/{right} "
        f"rev_m={matchup.get('revision')} "
        f"score={score.get('status')}/{score.get('left')}/{score.get('right')} "
        f"rev_s={score.get('revision')}"
    )


def show_api() -> int:
    print(f"api={_api_base()}")
    try:
        health, status = _get_json("/health")
    except Exception as exc:
        print(f"GET /health unreachable ({sanitize_control_plane_text(str(exc))})")
        return 2
    print(f"GET /health {status} {health}")
    diag, dstatus = _get_json("/api/diagnostics/orchestrator")
    print(f"GET /api/diagnostics/orchestrator {dstatus}")
    print(
        f"  orchestrator_enabled={diag.get('orchestrator_enabled')} "
        f"sync_ok={diag.get('last_sync_ok')} "
        f"reconcile_at={diag.get('last_reconcile_at')} "
        f"discovered={diag.get('discovered_count')} "
        f"upcoming={diag.get('upcoming_count')} live={diag.get('live_count')} "
        f"ended={diag.get('ended_count')} workers={diag.get('active_worker_count')}"
    )
    if diag.get("last_sync_error"):
        print(f"  last_sync_error={diag.get('last_sync_error')}")
    for worker in diag.get("workers") or []:
        print(
            f"  worker yt={worker.get('broadcast_id')} state={worker.get('state')} "
            f"frames={worker.get('frames_emitted')} retries={worker.get('retry_count')} "
            f"error={worker.get('last_error') or '-'}"
        )
    games, gstatus = _get_json("/api/games?limit=50")
    print(f"GET /api/games {gstatus} total={games.get('total')}")
    for game in games.get("games") or []:
        _print_game(game)
    return 0 if status == 200 and dstatus == 200 and gstatus == 200 else 2


def show_game(game_id: str) -> int:
    detail, status = _get_json(f"/api/games/{game_id}")
    print(f"GET /api/games/{game_id} {status}")
    if status != 200:
        print(f"  {detail}")
        return 2
    _print_game(detail)
    timeline, tstatus = _get_json(f"/api/games/{game_id}/score-events")
    print(f"GET /api/games/{game_id}/score-events {tstatus} total={timeline.get('total')}")
    for event in timeline.get("events") or []:
        print(
            f"  r{event.get('revision')} {event.get('previous_left')}-{event.get('previous_right')} "
            f"-> {event.get('left')}-{event.get('right')} init={event.get('initialization')} "
            f"at={event.get('confirmed_at')}"
        )
    return 0


def discover() -> int:
    from lsgc.broadcast.config import load_runtime_config
    from lsgc.broadcast.youtube import YouTubeDataApiClient

    try:
        config = load_runtime_config()
        client = YouTubeDataApiClient(
            api_key=config.api_key, access_token=config.access_token
        )
    except Exception as exc:
        print(f"discover: {sanitize_control_plane_text(str(exc))}")
        return 2
    for channel in config.channels:
        print(f"channel={channel.channel_id}")
        try:
            found = client.list_channel_broadcasts(channel.channel_id)
        except Exception as exc:
            print(f"  api_error={sanitize_control_plane_text(str(exc))}")
            return 2
        print(f"  discovered={len(found)}")
        for item in found:
            scheduled = item.scheduled_start.isoformat() if item.scheduled_start else "-"
            print(
                f"  yt={item.broadcast_id} lifecycle={item.lifecycle.value} "
                f"scheduled={scheduled}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--game-id")
    args = parser.parse_args(argv)
    code = show_api()
    if args.game_id:
        code = show_game(args.game_id) or code
    if args.discover:
        code = discover() or code
    return code


if __name__ == "__main__":
    raise SystemExit(main())
