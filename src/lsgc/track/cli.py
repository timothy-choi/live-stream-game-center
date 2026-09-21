"""Developer tooling for temporal score validation.

    python -m lsgc.track replay SEQUENCE.json
    python -m lsgc.track live WATCH_URL --profile P --reference R --glyphs G \\
        --expect-channel UC...

`replay` feeds a JSON array of synthetic/raw observations (no video).
`live` is a thin session wrapper: LiveFrameSession -> M3/M6 pipeline ->
TemporalScoreValidator. Not a product service. Acquisition ENDED does not
print FINAL.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from lsgc.score.models import RawScoreObservation, ScoreRead, UnreadableReason
from lsgc.track.errors import TrackError
from lsgc.track.policy import ConfirmationPolicy
from lsgc.track.validator import TemporalScoreValidator

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.track",
        description="Temporal validation of raw score observations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay", help="replay a JSON observation sequence")
    replay.add_argument("sequence", help="JSON file: {policy?, events: [...]}")
    replay.add_argument("--required", type=int, default=None)
    replay.add_argument("--min-span", type=float, default=None)
    replay.add_argument("--max-gap", type=float, default=None)

    live = sub.add_parser("live", help="one live watch URL through M3/M6/M7")
    live.add_argument("url")
    live.add_argument("--profile", required=True)
    live.add_argument("--reference", required=True)
    live.add_argument("--glyphs", required=True)
    live.add_argument("--expect-channel", required=True)
    live.add_argument("--interval", type=float, default=2.0)
    live.add_argument("--duration", type=float, default=900.0)
    live.add_argument("--max-height", type=int, default=720)
    return parser


def _parse_side(value: object) -> ScoreRead:
    if value is None:
        return ScoreRead.unreadable(UnreadableReason.NO_DIGITS)
    if isinstance(value, int):
        return ScoreRead.of(value, confidence=0.99, margin=0.2, raw_digits=str(value))
    if isinstance(value, dict):
        if value.get("readable") is False or value.get("value") is None:
            reason = (
                UnreadableReason(value["reason"])
                if value.get("reason")
                else UnreadableReason.NO_DIGITS
            )
            return ScoreRead.unreadable(reason)
        return ScoreRead.of(
            int(value["value"]),
            confidence=float(value.get("confidence", 0.99)),
            margin=float(value.get("margin", 0.2)),
            raw_digits=str(value.get("raw_digits", value["value"])),
        )
    raise TrackError(f"Cannot parse score side {value!r}")


def _observation_from_event(event: dict, *, index: int) -> RawScoreObservation:
    left = _parse_side(event.get("left"))
    right = _parse_side(event.get("right"))
    return RawScoreObservation(
        frame_index=event.get("frame_index", index),
        timestamp_s=event.get("timestamp_s", float(index)),
        received_at=None,
        left=left,
        right=right,
        detection_present=event.get("detection_present", True),
        detection_score=float(event.get("detection_score", 0.99)),
        detection_threshold=float(event.get("detection_threshold", 0.55)),
        channel_id=str(event.get("channel_id", "UC_replay")),
        profile_id=str(event.get("profile_id", "replay")),
        profile_revision=int(event.get("profile_revision", 1)),
        frame_width=int(event.get("frame_width", 1280)),
        frame_height=int(event.get("frame_height", 720)),
        source_id=event.get("source_id"),
        source_url=event.get("source_url"),
        source_epoch=event.get("source_epoch"),
    )


def _print_result(result) -> None:
    print(result.summary(), flush=True)


def _replay(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.sequence).read_text(encoding="utf-8"))
    policy_data = payload.get("policy", {})
    required = args.required if args.required is not None else policy_data.get("required_observations", 2)
    min_span = args.min_span if args.min_span is not None else policy_data.get("min_span_s", 2.0)
    max_gap = args.max_gap if args.max_gap is not None else policy_data.get("max_candidate_gap_s", 8.0)
    policy = ConfirmationPolicy(
        required_observations=int(required),
        min_span_s=float(min_span),
        max_candidate_gap_s=None if max_gap in (None, "none") else float(max_gap),
    )
    validator = TemporalScoreValidator(policy)
    print(f"policy {policy.summary()}", flush=True)
    events = payload.get("events", payload if isinstance(payload, list) else [])
    for index, event in enumerate(events):
        kind = event.get("kind", "observation") if isinstance(event, dict) else "observation"
        if kind in {"end", "freeze"}:
            _print_result(validator.end_acquisition())
            continue
        if kind == "reset":
            _print_result(validator.reset())
            continue
        if kind in {"absent", "absence"}:
            result = validator.note_absence(
                frame_index=event.get("frame_index", index),
                timestamp_s=event.get("timestamp_s"),
                source_epoch=event.get("source_epoch"),
            )
            _print_result(result)
            continue
        observation = _observation_from_event(event, index=index)
        _print_result(validator.apply(observation))
    print(validator.state.summary(), flush=True)
    for change in validator.history:
        print(change.summary(), flush=True)
    print(
        "Trusted is temporally confirmed visual score, not official/final/persisted.",
        flush=True,
    )
    return EXIT_OK


def _live(args: argparse.Namespace) -> int:
    from lsgc.calibration import load_profile
    from lsgc.live import LiveFrameSession
    from lsgc.live.errors import SessionFailed
    from lsgc.live.sanitize import sanitize_text
    from lsgc.live.state import SessionState
    from lsgc.score import ScoreObservationPipeline, ScoreReader, load_glyphs
    from lsgc.vision import ScoreboardDetector, load_reference

    profile = load_profile(args.profile, expect_channel=args.expect_channel)
    reference = load_reference(args.reference, expect_channel=args.expect_channel)
    glyphs = load_glyphs(args.glyphs, expect_channel=args.expect_channel)
    pipeline = ScoreObservationPipeline(
        ScoreboardDetector(profile, reference, expect_channel=args.expect_channel),
        ScoreReader(glyphs),
    )
    validator = TemporalScoreValidator()
    session = LiveFrameSession(args.url, interval_s=args.interval, max_height=args.max_height)

    def stop(*_args: object) -> None:
        session.cancel()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    import threading

    timer = None
    if args.duration > 0:
        timer = threading.Timer(args.duration, session.cancel)
        timer.daemon = True
        timer.start()

    print(
        f"track-live watch={args.url} interval={args.interval:g}s "
        f"policy={validator.policy.summary()}",
        flush=True,
    )
    try:
        for frame in session.frames():
            observation = pipeline.observe_frame(frame)
            if observation is None:
                result = validator.note_absence(
                    frame_index=frame.index,
                    timestamp_s=frame.timestamp_s,
                    source_epoch=frame.extra.get("source_epoch") if frame.extra else None,
                )
            else:
                result = validator.apply(observation)
            raw = (
                "RAW none"
                if observation is None
                else f"RAW {observation.left.value}-{observation.right.value}"
            )
            print(f"{raw} {result.summary()}", flush=True)
    except SessionFailed as exc:
        print(f"SESSION FAILED: {sanitize_text(str(exc))}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if timer is not None:
            timer.cancel()
        session.cancel()

    end = validator.end_acquisition()
    print(end.summary(), flush=True)
    print(
        f"final_session={session.state.value} {validator.state.summary()} "
        "(acquisition end is not a game-final)",
        flush=True,
    )
    if session.state == SessionState.FAILED:
        return EXIT_ERROR
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            return _replay(args)
        if args.command == "live":
            return _live(args)
    except (TrackError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
