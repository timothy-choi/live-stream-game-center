"""Minimal developer tooling for reference evidence and detection.

    python -m lsgc.vision build-reference PROFILE FRAME [FRAME ...] --out REF.json
    python -m lsgc.vision show            REF.json
    python -m lsgc.vision detect          PROFILE REF.json FRAME [--overlay]

Developer mechanism, not product UI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

from lsgc.calibration import CalibrationError, load_profile_unchecked
from lsgc.vision.detector import DEFAULT_THRESHOLD, ScoreboardDetector
from lsgc.vision.errors import VisionError
from lsgc.vision.overlay import ARTIFACT_DIR, save_detection
from lsgc.vision.reference import (
    build_reference,
    load_reference_unchecked,
    save_reference,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2
EXIT_ABSENT = 3
"""`detect` exits 3 when the scoreboard is absent, so shell checks can branch
on the verdict without parsing stdout."""


def _read_images(paths: list[str]) -> list:
    images = []
    for path in paths:
        image = cv2.imread(path)
        if image is None:
            raise VisionError(f"Could not read image: {path}")
        images.append(image)
    return images


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.vision",
        description="Build calibrated reference evidence and detect scoreboard presence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-reference", help="sample reference patches from frames")
    build.add_argument("profile", help="path to a calibration profile JSON")
    build.add_argument("frames", nargs="+", help="frames showing the normal scoreboard")
    build.add_argument("--out", required=True)
    build.add_argument("--notes", default="")
    build.add_argument("--expect-channel", default=None)

    show = sub.add_parser("show", help="print stored reference evidence")
    show.add_argument("reference")
    show.add_argument("--expect-channel", default=None)

    detect = sub.add_parser("detect", help="check one frame for the calibrated scoreboard")
    detect.add_argument("profile")
    detect.add_argument("reference")
    detect.add_argument("frame")
    detect.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    detect.add_argument("--expect-channel", default=None)
    detect.add_argument(
        "--overlay", nargs="?", const="", default=None, metavar="PATH",
        help=f"write a diagnostic image (default under {ARTIFACT_DIR})",
    )
    return parser


def _build(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(args.profile)
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
    reference = build_reference(_read_images(args.frames), profile, notes=args.notes)
    print(reference.summary())
    print(f"\nWrote {save_reference(reference, args.out)}")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    reference = load_reference_unchecked(args.reference)
    if args.expect_channel and reference.channel_id != args.expect_channel:
        raise VisionError(
            f"Reference is for channel {reference.channel_id!r}, not {args.expect_channel!r}."
        )
    print(reference.summary())
    return EXIT_OK


def _detect(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(args.profile)
    reference = load_reference_unchecked(args.reference)
    detector = ScoreboardDetector(
        profile, reference, threshold=args.threshold, expect_channel=args.expect_channel
    )
    image = _read_images([args.frame])[0]
    result = detector.detect(image)
    print(result.summary())

    if args.overlay is not None:
        stem = Path(args.frame).stem
        destination = Path(args.overlay) if args.overlay else (
            ARTIFACT_DIR / f"{stem}_{'present' if result.present else 'absent'}.png"
        )
        print(f"Wrote {save_detection(image, result, destination)}")
    return EXIT_OK if result.present else EXIT_ABSENT


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"build-reference": _build, "show": _show, "detect": _detect}[args.command]
    try:
        return handler(args)
    except (VisionError, CalibrationError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
