"""Connected-channel and YouTube credential configuration.

Credentials come from the environment. Production channel ids are never
hard-coded. Multiple channels may be listed; each is an explicit config entry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lsgc.broadcast.errors import BroadcastConfigError
from lsgc.broadcast.models import ConnectedChannel

YOUTUBE_API_KEY_ENV = "LSGC_YOUTUBE_API_KEY"
YOUTUBE_ACCESS_TOKEN_ENV = "LSGC_YOUTUBE_ACCESS_TOKEN"
YOUTUBE_CHANNEL_ID_ENV = "LSGC_YOUTUBE_CHANNEL_ID"
YOUTUBE_CHANNEL_IDS_ENV = "LSGC_YOUTUBE_CHANNEL_IDS"
CALIBRATION_DIR_ENV = "LSGC_CALIBRATION_DIR"
SYNC_INTERVAL_ENV = "LSGC_BROADCAST_SYNC_INTERVAL_S"

# search.list has a scarce daily bucket (100 calls/day, 1 per page). Two
# eventType queries per cycle => default 30 minutes so a process stays under
# that bucket with headroom for pagination and retries.
DEFAULT_SYNC_INTERVAL_S = 1800.0
DEFAULT_OAUTH_SYNC_INTERVAL_S = 120.0
DEFAULT_WORKER_RETRY_INITIAL_S = 15.0
DEFAULT_WORKER_RETRY_MAX_S = 300.0
DEFAULT_FRAME_INTERVAL_S = 2.0


@dataclass(frozen=True)
class BroadcastRuntimeConfig:
    channels: tuple[ConnectedChannel, ...]
    api_key: str | None
    access_token: str | None
    sync_interval_s: float
    calibration_dir: Path | None
    frame_interval_s: float = DEFAULT_FRAME_INTERVAL_S
    worker_retry_initial_s: float = DEFAULT_WORKER_RETRY_INITIAL_S
    worker_retry_max_s: float = DEFAULT_WORKER_RETRY_MAX_S


def load_connected_channels(*, environ: dict[str, str] | None = None) -> tuple[ConnectedChannel, ...]:
    env = os.environ if environ is None else environ
    raw_many = (env.get(YOUTUBE_CHANNEL_IDS_ENV) or "").strip()
    raw_one = (env.get(YOUTUBE_CHANNEL_ID_ENV) or "").strip()
    parts: list[str] = []
    if raw_many:
        parts.extend(p.strip() for p in raw_many.split(",") if p.strip())
    elif raw_one:
        parts.append(raw_one)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    channels: list[ConnectedChannel] = []
    for channel_id in parts:
        if channel_id in seen:
            continue
        seen.add(channel_id)
        channels.append(ConnectedChannel(channel_id=channel_id))
    return tuple(channels)


def load_runtime_config(*, environ: dict[str, str] | None = None) -> BroadcastRuntimeConfig:
    env = os.environ if environ is None else environ
    channels = load_connected_channels(environ=env)
    api_key = (env.get(YOUTUBE_API_KEY_ENV) or "").strip() or None
    access_token = (env.get(YOUTUBE_ACCESS_TOKEN_ENV) or "").strip() or None
    interval_raw = (env.get(SYNC_INTERVAL_ENV) or "").strip()
    if interval_raw:
        try:
            interval = float(interval_raw)
        except ValueError as exc:
            raise BroadcastConfigError(
                f"{SYNC_INTERVAL_ENV} must be a number of seconds"
            ) from exc
    else:
        oauth_app = bool((env.get("LSGC_GOOGLE_CLIENT_ID") or "").strip())
        interval = (
            DEFAULT_OAUTH_SYNC_INTERVAL_S
            if access_token or oauth_app
            else DEFAULT_SYNC_INTERVAL_S
        )
    if interval <= 0:
        raise BroadcastConfigError("sync interval must be > 0")
    calib_raw = (env.get(CALIBRATION_DIR_ENV) or "").strip() or None
    return BroadcastRuntimeConfig(
        channels=channels,
        api_key=api_key,
        access_token=access_token,
        sync_interval_s=interval,
        calibration_dir=Path(calib_raw) if calib_raw else None,
    )
