"""Developer tooling for digit glyphs and single-frame score reads.

    python -m lsgc.score build-glyphs PROFILE FRAME=L-R [FRAME=L-R ...] --out GLYPHS.json
    python -m lsgc.score show GLYPHS.json
    python -m lsgc.score read PROFILE GLYPHS FRAME [--reference REF.json]

`read` with a scoreboard reference is gated by M3: ABSENT frames emit nothing
and exit 3. Without a reference it reads the crops directly (debug only).

Developer mechanism, not product UI. Not trusted score state.
"""

from __future__ import annotations

import argparse
import sys

import cv2

from lsgc.calibration import CalibrationError, load_profile_unchecked
from lsgc.score.errors import ScoreError
from lsgc.score.glyphs import build_glyphs, load_glyphs_unchecked, save_glyphs
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.score.reader import DEFAULT_MIN_MARGIN, DEFAULT_MIN_SCORE, ScoreReader
from lsgc.vision.detector import ScoreboardDetector
from lsgc.vision.errors import VisionError
from lsgc.vision.reference import load_reference_unchecked

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2
EXIT_ABSENT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.score",
        description="Build digit glyphs and read calibrated score crops.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-glyphs", help="average digit patches from labelled frames")
    build.add_argument("profile")
    build.add_argument(
        "samples",
        nargs="+",
        help="FRAME=LEFT-RIGHT, e.g. artifacts/m3/t00040.000.png=0-0",
    )
    build.add_argument("--out", required=True)
    build.add_argument("--notes", default="")
    build.add_argument("--expect-channel", default=None)

    show = sub.add_parser("show", help="print stored digit glyphs")
    show.add_argument("glyphs")
    show.add_argument("--expect-channel", default=None)

    read = sub.add_parser("read", help="read scores from one frame")
    read.add_argument("profile")
    read.add_argument("glyphs")
    read.add_argument("frame")
    read.add_argument("--reference", default=None, help="M3 reference; gates on presence")
    read.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    read.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)
    read.add_argument("--expect-channel", default=None)
    return parser


def _parse_sample(spec: str):
    if "=" not in spec or "-" not in spec.split("=", 1)[1]:
        raise ScoreError(f"Sample must be FRAME=LEFT-RIGHT, got {spec!r}")
    path, scores = spec.split("=", 1)
    left_s, right_s = scores.split("-", 1)
    image = cv2.imread(path)
    if image is None:
        raise ScoreError(f"Could not read image: {path}")
    try:
        left, right = int(left_s), int(right_s)
    except ValueError as exc:
        raise ScoreError(f"Scores must be integers in {spec!r}") from exc
    if image.ndim != 3:
        raise ScoreError(f"Expected a colour frame, got shape {image.shape}")
    return image, left, right


def _build(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(args.profile)
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
    samples = [_parse_sample(spec) for spec in args.samples]
    glyphs = build_glyphs(samples, profile, notes=args.notes)
    save_glyphs(glyphs, args.out)
    print(glyphs.summary())
    print(f"wrote {args.out}")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    glyphs = load_glyphs_unchecked(args.glyphs)
    if args.expect_channel:
        glyphs.assert_channel(args.expect_channel)
    print(glyphs.summary())
    return EXIT_OK


def _read(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(args.profile)
    glyphs = load_glyphs_unchecked(args.glyphs)
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
        glyphs.assert_channel(args.expect_channel)
    image = cv2.imread(args.frame)
    if image is None:
        raise ScoreError(f"Could not read image: {args.frame}")
    reader = ScoreReader(
        glyphs, min_score=args.min_score, min_margin=args.min_margin
    )
    if args.reference:
        reference = load_reference_unchecked(args.reference)
        detector = ScoreboardDetector(profile, reference)
        observation = ScoreObservationPipeline(detector, reader).observe(image)
        if observation is None:
            print("ABSENT  (no score observation)")
            return EXIT_ABSENT
        print(observation.summary())
        return EXIT_OK
    left, right = reader.read_image(image, profile)
    print(f"L {left.summary()}")
    print(f"R {right.summary()}")
    print("ungated read — not an M3-present observation")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build-glyphs":
            return _build(args)
        if args.command == "show":
            return _show(args)
        if args.command == "read":
            return _read(args)
    except (ScoreError, CalibrationError, VisionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    parser.error(f"unknown command {args.command}")
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
