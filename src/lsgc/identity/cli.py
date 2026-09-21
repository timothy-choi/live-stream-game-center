"""Developer tooling for logo templates and identity reads.

    python -m lsgc.identity build-logos PROFILE FRAME=SIDE:TEAM ... --out LOGOS.json
    python -m lsgc.identity show LOGOS.json
    python -m lsgc.identity read PROFILE LOGOS FRAME [--reference REF.json]
    python -m lsgc.identity replay sequence.json

`read` with a scoreboard reference is gated by M3. Without a reference it
reads the crops directly (debug only). Not a confirmed matchup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from lsgc.calibration import CalibrationError, load_profile_unchecked
from lsgc.identity.errors import IdentityError
from lsgc.identity.logos import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    LogoRecognizer,
    build_logo_templates,
    extract_identity_crops,
    load_logo_templates_unchecked,
    save_logo_templates,
)
from lsgc.identity.models import MatchupObservation
from lsgc.identity.pipeline import MatchupObservationPipeline
from lsgc.identity.policy import MatchupConfirmationPolicy
from lsgc.identity.recognizer import IdentityRecognizer
from lsgc.identity.validator import TemporalMatchupValidator
from lsgc.vision.detector import ScoreboardDetector
from lsgc.vision.errors import VisionError
from lsgc.vision.reference import load_reference_unchecked

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2
EXIT_ABSENT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.identity",
        description="Build logo templates and read calibrated identity crops.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-logos", help="store construction crops as templates")
    build.add_argument("profile")
    build.add_argument(
        "samples",
        nargs="+",
        help="FRAME=SIDE:TEAM, e.g. artifacts/m3/t00240.000.png=L:SF",
    )
    build.add_argument("--out", required=True)
    build.add_argument("--notes", default="")
    build.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    build.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)
    build.add_argument("--expect-channel", default=None)

    show = sub.add_parser("show", help="print stored logo templates")
    show.add_argument("logos")
    show.add_argument("--expect-channel", default=None)

    read = sub.add_parser("read", help="read identity from one frame")
    read.add_argument("profile")
    read.add_argument("logos")
    read.add_argument("frame")
    read.add_argument("--reference", default=None, help="M3 reference; gates on presence")
    read.add_argument("--min-score", type=float, default=None)
    read.add_argument("--min-margin", type=float, default=None)
    read.add_argument("--expect-channel", default=None)

    replay = sub.add_parser("replay", help="replay a JSON matchup-observation sequence")
    replay.add_argument("sequence")
    replay.add_argument("--required", type=int, default=None)
    replay.add_argument("--min-span", type=float, default=None)
    replay.add_argument("--max-gap", type=float, default=None)
    return parser


def _parse_sample(spec: str):
    if "=" not in spec or ":" not in spec.split("=", 1)[1]:
        raise IdentityError(f"Sample must be FRAME=SIDE:TEAM, got {spec!r}")
    path, rest = spec.split("=", 1)
    side_s, team_id = rest.split(":", 1)
    side = side_s.strip().upper()
    if side not in {"L", "R", "LEFT", "RIGHT"}:
        raise IdentityError(f"SIDE must be L or R in {spec!r}")
    image = cv2.imread(path)
    if image is None:
        raise IdentityError(f"Could not read image: {path}")
    return image, ("L" if side.startswith("L") else "R"), team_id.strip().upper()


def _cmd_build(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(Path(args.profile))
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
    samples = []
    for spec in args.samples:
        image, side, team_id = _parse_sample(spec)
        left, right = extract_identity_crops(image, profile)
        samples.append((left if side == "L" else right, team_id))
    templates = build_logo_templates(
        profile,
        samples,
        min_score=args.min_score,
        min_margin=args.min_margin,
        notes=args.notes,
    )
    save_logo_templates(templates, Path(args.out))
    print(
        f"wrote {len(templates.templates)} templates for "
        f"{sorted(templates.supported_team_ids)} -> {args.out}"
    )
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    templates = load_logo_templates_unchecked(Path(args.logos))
    if args.expect_channel:
        templates.assert_channel(args.expect_channel)
    print(
        f"channel={templates.channel_id} profile={templates.profile_id} "
        f"min_score={templates.min_score} min_margin={templates.min_margin}"
    )
    for item in templates.templates:
        print(
            f"  {item.team_id} mean_bgr={[round(v, 1) for v in item.mean_bgr]} "
            f"patch={item.patch.shape}"
        )
    return EXIT_OK


def _format_side(obs) -> str:
    if obs.readable:
        margin = f" margin={obs.margin:.3f}" if obs.margin is not None else ""
        conf = f" conf={obs.confidence:.3f}" if obs.confidence is not None else ""
        return f"{obs.team_id} {obs.method.value}{conf}{margin}"
    extra = f" conf={obs.confidence:.3f}" if obs.confidence is not None else ""
    return f"UNKNOWN {obs.reason.value}{extra}"


def _cmd_read(args: argparse.Namespace) -> int:
    profile = load_profile_unchecked(Path(args.profile))
    if args.expect_channel:
        profile.assert_channel(args.expect_channel)
    templates = load_logo_templates_unchecked(Path(args.logos))
    templates.assert_matches(profile)
    image = cv2.imread(args.frame)
    if image is None:
        raise IdentityError(f"Could not read image: {args.frame}")
    logos = LogoRecognizer(
        templates, min_score=args.min_score, min_margin=args.min_margin
    )
    recognizer = IdentityRecognizer(profile, logos=logos)
    if args.reference:
        reference = load_reference_unchecked(Path(args.reference))
        detector = ScoreboardDetector(profile, reference)
        pipeline = MatchupObservationPipeline(detector, recognizer)
        from datetime import UTC, datetime

        from lsgc.models import Frame

        frame = Frame(
            index=0,
            image=image,
            width=image.shape[1],
            height=image.shape[0],
            timestamp_s=0.0,
            source_url="file://read",
            received_at=datetime.now(tz=UTC),
        )
        observation = pipeline.observe_frame(frame)
        if observation is None:
            print("ABSENT")
            return EXIT_ABSENT
        print(f"L {_format_side(observation.left)}")
        print(f"R {_format_side(observation.right)}")
        return EXIT_OK
    left, right = recognizer.recognize_image(image)
    print(f"L {_format_side(left)}")
    print(f"R {_format_side(right)}")
    return EXIT_OK


def _obs_from_event(event: dict, index: int) -> MatchupObservation | None:
    from lsgc.identity.models import IdentityMethod, TeamIdentityObservation, UnreadableIdentityReason

    if event.get("absent"):
        return None
    def side(raw: dict | str | None) -> TeamIdentityObservation:
        if raw is None or raw == "UNKNOWN":
            return TeamIdentityObservation.unknown(UnreadableIdentityReason.LOW_CONFIDENCE)
        if isinstance(raw, str):
            return TeamIdentityObservation.known(
                raw, method=IdentityMethod.LOGO, confidence=0.9, margin=0.3
            )
        if not raw.get("readable", True):
            return TeamIdentityObservation.unknown(
                UnreadableIdentityReason(raw.get("reason", "low_confidence"))
            )
        return TeamIdentityObservation.known(
            str(raw["team_id"]),
            method=IdentityMethod(raw.get("method", "logo")),
            confidence=float(raw.get("confidence", 0.9)),
            margin=float(raw.get("margin", 0.3)),
        )
    return MatchupObservation(
        frame_index=event.get("index", index),
        timestamp_s=event.get("ts"),
        received_at=None,
        left=side(event.get("left")),
        right=side(event.get("right")),
        detection_present=True,
        detection_score=0.99,
        detection_threshold=0.55,
        channel_id="cli",
        profile_id="cli",
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_epoch=event.get("epoch", 1),
    )


def _cmd_replay(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.sequence).read_text(encoding="utf-8"))
    policy_raw = payload.get("policy") or {}
    policy = MatchupConfirmationPolicy(
        required_observations=args.required or policy_raw.get("required_observations", 2),
        min_span_s=args.min_span if args.min_span is not None else policy_raw.get("min_span_s", 2.0),
        max_candidate_gap_s=(
            args.max_gap if args.max_gap is not None else policy_raw.get("max_candidate_gap_s", 8.0)
        ),
    )
    validator = TemporalMatchupValidator(policy)
    for index, event in enumerate(payload.get("events") or []):
        if event == "end" or (isinstance(event, dict) and event.get("end")):
            result = validator.end_acquisition()
        elif isinstance(event, dict) and event.get("absent"):
            result = validator.note_absence(
                frame_index=event.get("index", index),
                timestamp_s=event.get("ts"),
                source_epoch=event.get("epoch"),
            )
        else:
            result = validator.apply(_obs_from_event(event, index))
        pair = result.state.pair
        print(f"{result.outcome.value} confirmed={pair} rev={result.state.revision}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build-logos":
            return _cmd_build(args)
        if args.command == "show":
            return _cmd_show(args)
        if args.command == "read":
            return _cmd_read(args)
        if args.command == "replay":
            return _cmd_replay(args)
        parser.error(f"unknown command {args.command}")
    except (IdentityError, CalibrationError, VisionError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    return EXIT_USAGE
