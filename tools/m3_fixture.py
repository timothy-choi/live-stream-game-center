"""Developer tooling for the M3 validation set. Not part of the package.

Capturing frames needs YouTube, so this is run by hand and never by pytest.

    python tools/m3_fixture.py survey  --offsets 5,60,120 [--out DIR]
    python tools/m3_fixture.py capture [--manifest PATH] [--out DIR]
    python tools/m3_fixture.py sheet   --pattern 'artifacts/m3/t*.png'

`survey` grabs arbitrary offsets while deciding what to label; `capture`
re-grabs exactly the offsets the committed manifest lists, so the validation
set can be rebuilt on any machine. `sheet` tiles frames into a contact sheet so
many can be classified by eye at once.

Offsets are fixture metadata: they identify frames for regression, and are
never an input to detection.
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

MANIFEST_PATH = Path("tests/fixtures/m3_validation_set.json")
FRAME_DIR = Path("artifacts/m3")
"""Gitignored. Decoded Madden frames are never committed."""


def frame_path(directory: Path, offset_s: float) -> Path:
    return directory / f"t{offset_s:08.3f}.png"


def capture(offsets: list[float], source_url: str, directory: Path) -> list[float]:
    """Grab one frame per offset with bounded seeking. Returns the failures."""
    directory.mkdir(parents=True, exist_ok=True)
    source = resolve_youtube(source_url)
    print(f"resolved {source.source_id}: {len(source.fragment_urls)} segments, "
          f"{source.width}x{source.height}\n")

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


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def contact_sheet(paths: list[Path], out: Path, columns: int = 4, width: int = 420) -> Path:
    """Tile frames with their offsets burned in, for bulk visual labelling."""
    tiles = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        scale = width / image.shape[1]
        tile = cv2.resize(image, (width, int(image.shape[0] * scale)))
        cv2.rectangle(tile, (0, 0), (118, 20), (0, 0, 0), -1)
        cv2.putText(tile, path.stem, (3, 15), cv2.FONT_HERSHEY_PLAIN, 1.0, (0, 255, 255), 1)
        tiles.append(tile)

    if not tiles:
        raise SystemExit("no readable frames matched")

    import numpy as np

    height = tiles[0].shape[0]
    rows = []
    for start in range(0, len(tiles), columns):
        row = tiles[start : start + columns]
        while len(row) < columns:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet)
    print(f"{len(tiles)} frames ({height}px tall each) -> {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    survey = sub.add_parser("survey", help="capture arbitrary offsets")
    survey.add_argument("--offsets", required=True, help="comma-separated seconds")
    survey.add_argument("--url", default="https://www.youtube.com/watch?v=hBblAZD-ApY")
    survey.add_argument("--out", type=Path, default=FRAME_DIR)

    grab = sub.add_parser("capture", help="capture every offset in the manifest")
    grab.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    grab.add_argument("--out", type=Path, default=FRAME_DIR)

    sheet = sub.add_parser("sheet", help="tile frames into a contact sheet")
    sheet.add_argument("--pattern", default=str(FRAME_DIR / "t*.png"))
    sheet.add_argument("--out", type=Path, default=FRAME_DIR / "sheet.png")
    sheet.add_argument("--columns", type=int, default=4)

    args = parser.parse_args(argv)

    if args.command == "survey":
        offsets = [float(part) for part in args.offsets.split(",") if part.strip()]
        capture(offsets, args.url, args.out)
    elif args.command == "capture":
        manifest = load_manifest(args.manifest)
        offsets = [float(s["offset_s"]) for s in manifest["samples"]]
        failed = capture(offsets, manifest["source_url"], args.out)
        if failed:
            print(f"\n{len(failed)} offsets failed: {failed}")
            return 1
    else:
        paths = sorted(Path().glob(args.pattern.replace("./", "")))
        contact_sheet(paths, args.out, columns=args.columns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
