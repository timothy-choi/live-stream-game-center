"""Minimal developer calibration tooling.

    python -m lsgc.calibration create  ...   author and write a profile
    python -m lsgc.calibration show    ...   print a stored profile
    python -m lsgc.calibration overlay ...   draw a profile over an image

This is a developer mechanism for producing fixtures, not the product's
calibration UI. Regions are given as `x,y,width,height`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from lsgc.calibration.errors import CalibrationError
from lsgc.calibration.geometry import Region
from lsgc.calibration.overlay import ARTIFACT_DIR, save_overlay
from lsgc.calibration.profile import CalibrationProfile, IdentityPresentation
from lsgc.calibration.storage import load_profile_unchecked, save_profile

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CALIBRATION = 2


def parse_region(text: str) -> Region:
    """Parse `x,y,width,height`."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Expected 'x,y,width,height', got {text!r}"
        )
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"Region values must be integers: {text!r}") from None
    try:
        return Region(*numbers)
    except CalibrationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _parse_presentation(text: str) -> IdentityPresentation:
    try:
        return IdentityPresentation.parse(text)
    except CalibrationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.calibration",
        description="Author and inspect channel-bound scoreboard calibration profiles.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="author a profile and write it to JSON")
    create.add_argument("--channel-id", required=True, help="stable platform channel id")
    create.add_argument("--profile-id", required=True, help="calibration lineage id")
    create.add_argument("--revision", type=int, default=1)
    create.add_argument("--reference-size", required=True, help="WIDTHxHEIGHT, e.g. 1280x720")
    create.add_argument("--scoreboard", required=True, type=parse_region)
    create.add_argument("--left-score", required=True, type=parse_region)
    create.add_argument("--right-score", required=True, type=parse_region)
    create.add_argument("--left-identity", required=True, type=parse_region)
    create.add_argument("--right-identity", required=True, type=parse_region)
    create.add_argument(
        "--left-identity-presentation", required=True, type=_parse_presentation,
        help="any of name/abbreviation/logo, comma separated",
    )
    create.add_argument(
        "--right-identity-presentation", required=True, type=_parse_presentation,
        help="any of name/abbreviation/logo, comma separated",
    )
    create.add_argument(
        "--anchor", action="append", default=[], type=parse_region,
        metavar="X,Y,W,H", help="stable anchor region; repeatable, at least one required",
    )
    create.add_argument(
        "--ignore", action="append", default=[], type=parse_region,
        metavar="X,Y,W,H", help="dynamic ignore region; repeatable",
    )
    create.add_argument("--notes", default="")
    create.add_argument("--out", required=True, help="path to write the JSON profile")

    show = sub.add_parser("show", help="print a stored profile")
    show.add_argument("profile", help="path to a JSON profile")
    show.add_argument("--expect-channel", default=None, help="fail if the channel differs")

    overlay = sub.add_parser("overlay", help="draw a profile over a representative image")
    overlay.add_argument("profile", help="path to a JSON profile")
    overlay.add_argument("image", help="representative frame to annotate")
    overlay.add_argument("--expect-channel", default=None)
    overlay.add_argument("--out", default=None, help=f"output path (default under {ARTIFACT_DIR})")

    return parser


def _parse_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Expected reference size as WIDTHxHEIGHT, got {text!r}"
        ) from None
    return width, height


def _create(args: argparse.Namespace) -> int:
    width, height = _parse_size(args.reference_size)
    profile = CalibrationProfile(
        profile_id=args.profile_id,
        channel_id=args.channel_id,
        revision=args.revision,
        reference_width=width,
        reference_height=height,
        scoreboard=args.scoreboard,
        left_score=args.left_score,
        right_score=args.right_score,
        left_identity=args.left_identity,
        right_identity=args.right_identity,
        left_identity_presentation=args.left_identity_presentation,
        right_identity_presentation=args.right_identity_presentation,
        stable_anchors=tuple(args.anchor),
        dynamic_ignore=tuple(args.ignore),
        created_at=datetime.now(UTC),
        notes=args.notes,
    )
    print(profile.summary())
    written = save_profile(profile, args.out)
    print(f"\nVALID. Wrote {written}")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(args.profile)
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
    print(profile.summary())
    return EXIT_OK


def _overlay(args: argparse.Namespace) -> int:
    import cv2

    profile = load_profile_unchecked(args.profile)
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)

    image = cv2.imread(args.image)
    if image is None:
        print(f"Could not read image: {args.image}", file=sys.stderr)
        return EXIT_USAGE

    height, width = image.shape[:2]
    out = Path(args.out) if args.out else (
        ARTIFACT_DIR / f"{profile.profile_id}_r{profile.revision}_{width}x{height}.png"
    )
    written = save_overlay(image, profile, out)
    print(
        f"Annotated {args.image} ({width}x{height}) with profile "
        f"{profile.profile_id} r{profile.revision} "
        f"(reference {profile.reference_width}x{profile.reference_height})\n"
        f"Wrote {written}"
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"create": _create, "show": _show, "overlay": _overlay}[args.command]
    try:
        return handler(args)
    except CalibrationError as exc:
        print(f"CALIBRATION ERROR: {exc}", file=sys.stderr)
        return EXIT_CALIBRATION


if __name__ == "__main__":
    raise SystemExit(main())
