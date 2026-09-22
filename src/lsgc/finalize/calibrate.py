"""Build a versioned FinalizationProfile from a completed-game screenshot.

CLI/backend tooling is the M12.5B calibration surface. A future UI can call
the same `calibrate_from_image` helper without changing runtime detection.

Example:

    python -m lsgc.finalize.calibrate \\
        --image completed.png --channel UC... --profile-id arcade-final \\
        --panel 200,40,880,640 \\
        --anchor 220,50,80,36 --anchor 980,50,80,36 --anchor 240,620,120,40 \\
        --left-score 360,180,70,48 --right-score 850,180,70,48 \\
        --ignore 400,90,480,50 --out artifacts/calibration/UC...
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from lsgc.calibration.geometry import Region
from lsgc.finalize.detector import CompletionDetector
from lsgc.finalize.profile import FinalizationProfile
from lsgc.finalize.reader import read_final_score
from lsgc.finalize.storage import save_finalization_profile
from lsgc.score.glyphs import load_glyphs
from lsgc.score.reader import ScoreReader
from lsgc.vision.reference import build_reference, save_reference


def _parse_region(text: str) -> Region:
    parts = [int(part.strip()) for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"region must be x,y,width,height, got {text!r}")
    return Region(x=parts[0], y=parts[1], width=parts[2], height=parts[3])


def calibrate_from_image(
    image: np.ndarray,
    *,
    channel_id: str,
    profile_id: str,
    completion_panel: Region,
    stable_anchors: tuple[Region, ...],
    left_final_score_region: Region,
    right_final_score_region: Region,
    dynamic_ignore: tuple[Region, ...] = (),
    notes: str = "",
    revision: int = 1,
) -> tuple[FinalizationProfile, object]:
    """Measure a completed-game screenshot into a versioned profile + reference."""
    height, width = image.shape[:2]
    profile = FinalizationProfile(
        profile_id=profile_id,
        channel_id=channel_id,
        revision=revision,
        reference_width=width,
        reference_height=height,
        completion_panel=completion_panel,
        stable_anchors=stable_anchors,
        left_final_score_region=left_final_score_region,
        right_final_score_region=right_final_score_region,
        dynamic_ignore=dynamic_ignore,
        created_at=datetime.now(tz=UTC),
        notes=notes,
    )
    reference = build_reference(
        [image],
        profile,
        notes="built from one completed-game calibration screenshot",
    )
    return profile, reference


def _preview(
    image: np.ndarray,
    profile: FinalizationProfile,
    reference: object,
    glyphs_path: Path | None,
) -> None:
    detector = CompletionDetector(profile, reference)  # type: ignore[arg-type]
    detection = detector.detect(image)
    print(detection.summary())
    if glyphs_path is None or not glyphs_path.is_file():
        return
    reader = ScoreReader(load_glyphs(glyphs_path, expect_channel=profile.channel_id))
    left, right = read_final_score(image, profile, reader)
    print(f"left_final  {left.summary()}")
    print(f"right_final {right.summary()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate a completed-game screenshot into a FinalizationProfile."
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--panel", required=True, help="x,y,w,h")
    parser.add_argument(
        "--anchor",
        action="append",
        required=True,
        dest="anchors",
        help="stable anchor x,y,w,h (repeat; at least two)",
    )
    parser.add_argument("--left-score", required=True, help="x,y,w,h")
    parser.add_argument("--right-score", required=True, help="x,y,w,h")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        dest="ignores",
        help="dynamic-ignore x,y,w,h (repeat)",
    )
    parser.add_argument("--out", required=True, type=Path, help="channel calibration directory")
    parser.add_argument("--notes", default="")
    parser.add_argument("--glyphs", type=Path, default=None, help="optional glyphs.json for preview")
    parser.add_argument("--revision", type=int, default=1)
    args = parser.parse_args(argv)

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        print(f"error: could not read image {args.image}")
        return 2
    profile, reference = calibrate_from_image(
        image,
        channel_id=args.channel,
        profile_id=args.profile_id,
        completion_panel=_parse_region(args.panel),
        stable_anchors=tuple(_parse_region(item) for item in args.anchors),
        left_final_score_region=_parse_region(args.left_score),
        right_final_score_region=_parse_region(args.right_score),
        dynamic_ignore=tuple(_parse_region(item) for item in args.ignores),
        notes=args.notes,
        revision=args.revision,
    )
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    save_finalization_profile(profile, out / "finalization.json")
    save_reference(reference, out / "final_reference.json")
    print(profile.summary())
    print(f"wrote {out / 'finalization.json'}")
    print(f"wrote {out / 'final_reference.json'}")
    _preview(image, profile, reference, args.glyphs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
