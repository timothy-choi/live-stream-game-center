"""Capture 2-second score clusters for M7 default-policy replay.

    python tools/m7_fixture.py capture

Needs YouTube. Writes gitignored PNGs next to the M3/M5 frames. Never run by
pytest. Offsets come from tests/fixtures/m7_dense_replay.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lsgc.decoder import iter_frames  # noqa: E402
from lsgc.errors import IngestionError  # noqa: E402
from lsgc.resolver import resolve_youtube  # noqa: E402

MANIFEST = Path("tests/fixtures/m7_dense_replay.json")
CADENCE = 2.0


def frame_path(directory: Path, offset_s: float) -> Path:
    return directory / f"t{offset_s:08.3f}.png"


def cluster_offsets(base_s: float, cadence: float = CADENCE) -> list[float]:
    return [base_s, base_s + cadence, base_s + 2 * cadence]


def capture_offsets(offsets: list[float], source_url: str, directory: Path) -> list[float]:
    directory.mkdir(parents=True, exist_ok=True)
    source = resolve_youtube(source_url)
    print(f"resolved {source.source_id}: {len(source.fragment_urls)} segments, "
          f"{source.width}x{source.height}")
    failed: list[float] = []
    for offset in offsets:
        destination = frame_path(directory, offset)
        if destination.exists():
            print(f"  t={offset:<8.1f} cached")
            continue
        try:
            frame = next(iter(iter_frames(
                source, count=1, interval_s=0.0, start_at_s=offset, timeout_s=60.0
            )))
        except (IngestionError, StopIteration) as exc:
            print(f"  t={offset:<8.1f} FAILED: {exc}")
            failed.append(offset)
            continue
        cv2.imwrite(str(destination), frame.image)
        print(f"  t={offset:<8.1f} pts={frame.timestamp_s:<10.3f} -> {destination.name}")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture M7 2s score clusters")
    parser.add_argument("command", choices=["capture"])
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cadence = float(manifest.get("cadence_s", CADENCE))
    all_failed: list[tuple[str, float]] = []
    for rec in manifest["recordings"]:
        offsets: list[float] = []
        for cluster in rec["clusters"]:
            offsets.extend(cluster_offsets(float(cluster["base_s"]), cadence))
        print(f"\n=== {rec['key']} {rec['video_id']} ===")
        failed = capture_offsets(sorted(set(offsets)), rec["source_url"], Path(rec["frames_dir"]))
        all_failed.extend((rec["key"], off) for off in failed)
    if all_failed:
        print("FAILED", all_failed)
        return 1
    print("\nall clusters present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
