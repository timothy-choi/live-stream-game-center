"""Optional real YouTube Data API metadata validation for M9.

    LSGC_YOUTUBE_CHANNEL_ID=UC... LSGC_YOUTUBE_API_KEY=... \\
      python tools/m9_validate.py

Does not download video, does not print credentials, and does not require a
live broadcast to succeed. pytest never runs this.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from lsgc.broadcast.config import load_runtime_config  # noqa: E402
from lsgc.broadcast.models import PlatformLifecycle  # noqa: E402
from lsgc.broadcast.sanitize import sanitize_control_plane_text  # noqa: E402
from lsgc.broadcast.youtube import YouTubeDataApiClient  # noqa: E402


def main() -> int:
    try:
        config = load_runtime_config()
        client = YouTubeDataApiClient(
            api_key=config.api_key, access_token=config.access_token
        )
    except Exception as exc:
        print(f"skip: {sanitize_control_plane_text(str(exc))}")
        return 2

    total = Counter()
    for channel in config.channels:
        print(f"channel={channel.channel_id}")
        try:
            found = client.list_channel_broadcasts(channel.channel_id)
        except Exception as exc:
            print(f"  api_error={sanitize_control_plane_text(str(exc))}")
            return 2
        counts = Counter(item.lifecycle.value for item in found)
        total.update(counts)
        print(
            f"  discovered={len(found)} "
            f"upcoming={counts.get(PlatformLifecycle.UPCOMING.value, 0)} "
            f"live={counts.get(PlatformLifecycle.LIVE.value, 0)} "
            f"ended={counts.get(PlatformLifecycle.ENDED.value, 0)}"
        )
        for item in found:
            scheduled = (
                item.scheduled_start.isoformat() if item.scheduled_start else "-"
            )
            print(
                f"  id={item.broadcast_id} lifecycle={item.lifecycle.value} "
                f"channel={item.channel_id} scheduled={scheduled}"
            )
            if item.channel_id != channel.channel_id:
                print("  ERROR: channel_id mismatch")
                return 1
        ids = [item.broadcast_id for item in found]
        if len(ids) != len(set(ids)):
            print("  ERROR: duplicate broadcast ids")
            return 1
    if total.get(PlatformLifecycle.LIVE.value, 0) == 0:
        print("limitation: no LIVE broadcast during this metadata check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
