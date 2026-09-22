"""Observe the live product path. Does not start tracking.

    python tools/live_e2e.py --check
    python tools/live_e2e.py --discover
    python tools/live_e2e.py --watch

Required environment for --discover/--watch:

    DATABASE_URL
    LSGC_YOUTUBE_CHANNEL_ID
    LSGC_YOUTUBE_API_KEY and/or LSGC_YOUTUBE_ACCESS_TOKEN
    LSGC_CALIBRATION_DIR
    LSGC_API_BASE_URL   (default http://127.0.0.1:8000)

This is a read-only observer. Opening it does not call ensure_tracking,
LiveFrameSession, or the vision pipeline. pytest never runs this.

Credentials, signed media URLs, and raw OCR are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
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

from lsgc.broadcast.calibration import FilesystemCalibrationLookup  # noqa: E402
from lsgc.broadcast.config import (  # noqa: E402
    CALIBRATION_DIR_ENV,
    YOUTUBE_ACCESS_TOKEN_ENV,
    YOUTUBE_API_KEY_ENV,
    YOUTUBE_CHANNEL_ID_ENV,
    load_runtime_config,
)
from lsgc.broadcast.models import PlatformLifecycle  # noqa: E402
from lsgc.broadcast.sanitize import sanitize_control_plane_text  # noqa: E402
from lsgc.broadcast.youtube import YouTubeDataApiClient  # noqa: E402

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


def _present(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _get_json(path: str, timeout: float = 10.0) -> Any:
    url = path if path.startswith("http") else urljoin(_api_base() + "/", path.lstrip("/"))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": sanitize_control_plane_text(raw[:200])}
        return payload, exc.code


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


def _game_line(game: dict[str, Any]) -> str:
    score = game.get("score") or {}
    matchup = game.get("matchup") or {}
    left = (matchup.get("left_team") or {}).get("id") or "TBD"
    right = (matchup.get("right_team") or {}).get("id") or "TBD"
    sl = score.get("left")
    sr = score.get("right")
    score_text = (
        f"{sl}-{sr} r{score.get('revision', 0)}"
        if score.get("status") == "TRUSTED" and sl is not None and sr is not None
        else f"UNCONFIRMED r{score.get('revision', 0)}"
    )
    return (
        f"id={game.get('id')} yt={game.get('external_broadcast_id')} "
        f"lifecycle={game.get('platform_lifecycle')} "
        f"tracking={game.get('tracking_status')} "
        f"matchup={matchup.get('status')}/{left}/{right} "
        f"score={score.get('status')}/{score_text}"
    )


def check_prereqs() -> int:
    print("=== live e2e prerequisites ===")
    rows = [
        ("DATABASE_URL", _present("DATABASE_URL")),
        (YOUTUBE_CHANNEL_ID_ENV, _present(YOUTUBE_CHANNEL_ID_ENV)),
        (YOUTUBE_API_KEY_ENV, _present(YOUTUBE_API_KEY_ENV)),
        (YOUTUBE_ACCESS_TOKEN_ENV, _present(YOUTUBE_ACCESS_TOKEN_ENV)),
        (CALIBRATION_DIR_ENV, _present(CALIBRATION_DIR_ENV)),
        ("LSGC_RUN_ORCHESTRATOR", os.environ.get("LSGC_RUN_ORCHESTRATOR") or "unset"),
        ("LSGC_CORS_ORIGINS", "set" if _present("LSGC_CORS_ORIGINS") else "unset"),
        ("LSGC_BROADCAST_SYNC_INTERVAL_S", os.environ.get("LSGC_BROADCAST_SYNC_INTERVAL_S") or "default"),
        ("LSGC_API_BASE_URL", _api_base()),
    ]
    ok = True
    for name, value in rows:
        print(f"{name}: {value}")
    if not _present("DATABASE_URL"):
        print("missing DATABASE_URL")
        ok = False
    if not _present(YOUTUBE_CHANNEL_ID_ENV):
        print(f"missing {YOUTUBE_CHANNEL_ID_ENV}")
        ok = False
    if not (_present(YOUTUBE_API_KEY_ENV) or _present(YOUTUBE_ACCESS_TOKEN_ENV)):
        print(f"missing {YOUTUBE_API_KEY_ENV} or {YOUTUBE_ACCESS_TOKEN_ENV}")
        ok = False
    if not _present(CALIBRATION_DIR_ENV):
        print(f"missing {CALIBRATION_DIR_ENV}")
        ok = False
    else:
        channel = (os.environ.get(YOUTUBE_CHANNEL_ID_ENV) or "").strip()
        lookup = FilesystemCalibrationLookup(Path(os.environ[CALIBRATION_DIR_ENV]))
        bundle = lookup.get(channel) if channel else None
        if bundle is None:
            print("calibration: NOT_READY (directory did not load for this channel)")
            ok = False
        else:
            logos = 0 if bundle.logos is None else len(bundle.logos.templates)
            print(
                f"calibration: ready profile={bundle.profile.profile_id} "
                f"rev={bundle.profile.revision} logos={logos}"
            )
    try:
        payload, status = _get_json("/health")
        _assert_no_secrets(payload)
        print(f"GET /health: {status} {payload}")
        if status != 200 or not payload.get("alive"):
            ok = False
    except Exception as exc:
        print(f"GET /health: unreachable ({sanitize_control_plane_text(str(exc))})")
        print("start the API in the same process as the orchestrator:")
        print("  LSGC_RUN_ORCHESTRATOR=true ./.venv/bin/python -m lsgc.api")
        ok = False
    return 0 if ok else 2


def discover() -> int:
    try:
        config = load_runtime_config()
        client = YouTubeDataApiClient(
            api_key=config.api_key, access_token=config.access_token
        )
    except Exception as exc:
        print(f"discover: {sanitize_control_plane_text(str(exc))}")
        return 2
    found_any = False
    for channel in config.channels:
        print(f"channel={channel.channel_id}")
        try:
            found = client.list_channel_broadcasts(channel.channel_id)
        except Exception as exc:
            print(f"  api_error={sanitize_control_plane_text(str(exc))}")
            return 2
        found_any = True
        print(f"  discovered={len(found)}")
        for item in found:
            scheduled = item.scheduled_start.isoformat() if item.scheduled_start else "-"
            print(
                f"  yt={item.broadcast_id} lifecycle={item.lifecycle.value} "
                f"scheduled={scheduled}"
            )
    if not found_any:
        return 2
    return 0


def _iter_sse(game_id: str, timeout_s: float):
    url = f"{_api_base()}/api/games/{game_id}/events"
    request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    context = ssl.create_default_context()
    deadline = time.monotonic() + timeout_s
    with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
        event_name = "message"
        data_lines: list[str] = []
        while time.monotonic() < deadline:
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            if text.startswith(":"):
                continue
            if text.startswith("event:"):
                event_name = text.split(":", 1)[1].strip()
                continue
            if text.startswith("data:"):
                data_lines.append(text.split(":", 1)[1].strip())
                continue
            if text.strip() == "" and data_lines:
                payload = json.loads("\n".join(data_lines))
                _assert_no_secrets(payload)
                yield event_name, payload
                event_name = "message"
                data_lines = []


def watch(timeout_s: float, poll_s: float) -> int:
    print("=== watching M11 for live product updates ===")
    print("this observer never starts tracking")
    deadline = time.monotonic() + timeout_s
    seen: dict[str, dict[str, Any]] = {}
    sse_types: dict[str, set[str]] = {}
    stages = {
        "discovered_in_api": False,
        "platform_live": False,
        "tracking": False,
        "sse_snapshot": False,
        "matchup_confirmed": False,
        "score_trusted": False,
        "sse_score_or_matchup": False,
    }
    subscribed: set[str] = set()

    while time.monotonic() < deadline:
        payload, status = _get_json("/api/games?limit=50")
        if status != 200:
            print(f"GET /api/games failed status={status}")
            time.sleep(poll_s)
            continue
        _assert_no_secrets(payload)
        games = payload.get("games") or []
        if games:
            stages["discovered_in_api"] = True
        for game in games:
            game_id = game["id"]
            prev = seen.get(game_id)
            line = _game_line(game)
            if prev is None or _game_line(prev) != line:
                print(f"api {line}")
            seen[game_id] = game
            if game.get("platform_lifecycle") == PlatformLifecycle.LIVE.value:
                stages["platform_live"] = True
            if game.get("tracking_status") == "TRACKING":
                stages["tracking"] = True
            if (game.get("matchup") or {}).get("status") in {"CONFIRMED", "CONFLICT"}:
                stages["matchup_confirmed"] = True
            if (game.get("score") or {}).get("status") == "TRUSTED":
                stages["score_trusted"] = True
            if game.get("platform_lifecycle") == "LIVE" and game_id not in subscribed:
                subscribed.add(game_id)
                print(f"sse subscribe game={game_id} (read-only)")
                try:
                    for event_name, event in _iter_sse(game_id, timeout_s=min(20.0, deadline - time.monotonic())):
                        kind = event.get("type") or event_name
                        sse_types.setdefault(game_id, set()).add(str(kind))
                        if kind == "GAME_SNAPSHOT":
                            stages["sse_snapshot"] = True
                        if kind in {"SCORE_CONFIRMED", "MATCHUP_CONFIRMED", "MATCHUP_CONFLICT"}:
                            stages["sse_score_or_matchup"] = True
                        print(
                            f"sse type={kind} seq={event.get('sequence')} "
                            f"score_rev={event.get('score_revision')} "
                            f"matchup_rev={event.get('matchup_revision')}"
                        )
                        if stages["score_trusted"] and stages["matchup_confirmed"]:
                            break
                except Exception as exc:
                    print(f"sse wait: {sanitize_control_plane_text(str(exc))}")
        if all(stages.values()):
            break
        time.sleep(poll_s)

    print("=== stage results ===")
    failed = False
    for name, value in stages.items():
        print(f"{name}: {'PASS' if value else 'PENDING'}")
        if not value:
            failed = True
    return 0 if not failed else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="prerequisite check only")
    parser.add_argument("--discover", action="store_true", help="YouTube Data API list")
    parser.add_argument("--watch", action="store_true", help="poll API + SSE until confirmations")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--poll", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.check or args.discover or args.watch):
        args.check = True
    code = 0
    if args.check:
        code = check_prereqs() or code
    if args.discover:
        code = discover() or code
    if args.watch:
        code = watch(args.timeout, args.poll) or code
    return code


if __name__ == "__main__":
    raise SystemExit(main())
