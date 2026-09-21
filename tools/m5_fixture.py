"""Capture M5 cross-matchup frames. Not part of the package, never pytest.

    python tools/m5_fixture.py capture
    python tools/m5_fixture.py capture --key chargers

Offsets in the committed manifest are fixture metadata only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_m3_path = Path(__file__).resolve().parent / "m3_fixture.py"
_m3_spec = importlib.util.spec_from_file_location("m3_fixture", _m3_path)
_m3 = importlib.util.module_from_spec(_m3_spec)
assert _m3_spec.loader is not None
_m3_spec.loader.exec_module(_m3)
capture = _m3.capture

MANIFEST = Path("tests/fixtures/m5_validation_set.json")
FRAME_ROOT = Path("artifacts/m5")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capture"])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--out", type=Path, default=FRAME_ROOT)
    parser.add_argument("--key", default=None, help="capture only this recording key")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    failed_all: list[tuple[str, float]] = []
    for rec in manifest["recordings"]:
        if args.key and rec["key"] != args.key:
            continue
        offsets = [float(s["offset_s"]) for s in rec["samples"]]
        directory = args.out / rec["key"]
        print(f"\n# {rec['key']} {rec['video_id']} {rec['title']}")
        failed = capture(offsets, rec["source_url"], directory)
        failed_all.extend((rec["key"], off) for off in failed)
    if failed_all:
        print(f"\n{len(failed_all)} offsets failed: {failed_all}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
