"""Developer CLI for a bounded live-acquisition validation run.

    python -m lsgc.live_probe WATCH_URL --interval 2 --duration 900 \
        --profile PROFILE.json --reference REF.json --expect-channel UC...

This is a test harness, not a product service. It prints compact health and
optional M3 presence lines, then a summary. It does not persist scores, does
not OCR, and does not print resolved media URLs.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from datetime import UTC, datetime

from lsgc.live.errors import SessionError, SessionFailed
from lsgc.live.sanitize import sanitize_text
from lsgc.live.session import LiveFrameSession
from lsgc.live.state import SessionState

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2

DEFAULT_INTERVAL_S = 2.0
DEFAULT_DURATION_S = 900.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.live_probe",
        description=(
            "Run a bounded continuous acquisition session against one live "
            "YouTube watch URL. Optionally classify each Frame with the M3 "
            "scoreboard detector."
        ),
    )
    parser.add_argument("url", help="stable YouTube watch URL for the live broadcast")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"sampling cadence in seconds (default {DEFAULT_INTERVAL_S:g}; not a product optimum)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=(
            f"stop after this many wall-clock seconds (default {DEFAULT_DURATION_S:g}). "
            "0 means run until the broadcast ends or the process is interrupted."
        ),
    )
    parser.add_argument("--max-height", type=int, default=720)
    parser.add_argument("--stall-timeout", type=float, default=30.0)
    parser.add_argument("--profile", default=None, help="calibration profile JSON (optional M3)")
    parser.add_argument("--reference", default=None, help="scoreboard reference JSON (optional M3)")
    parser.add_argument("--expect-channel", default=None, help="required if --profile is set")
    parser.add_argument(
        "--print-every",
        type=int,
        default=1,
        help="print a diagnostic line every N emitted frames (default 1)",
    )
    return parser


def _build_detector(args: argparse.Namespace):
    profile_set = any([args.profile, args.reference, args.expect_channel])
    if not profile_set:
        return None
    if not (args.profile and args.reference and args.expect_channel):
        raise SystemExit(
            "M3 detection requires --profile, --reference and --expect-channel together."
        )
    from lsgc.calibration import load_profile
    from lsgc.vision import ScoreboardDetector, load_reference

    profile = load_profile(args.profile, expect_channel=args.expect_channel)
    reference = load_reference(args.reference, expect_channel=args.expect_channel)
    return ScoreboardDetector(profile, reference, expect_channel=args.expect_channel)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("interval must be > 0", file=sys.stderr)
        return EXIT_USAGE
    if args.duration < 0:
        print("duration must be >= 0", file=sys.stderr)
        return EXIT_USAGE

    try:
        detector = _build_detector(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:
        print(sanitize_text(f"Could not load detector: {exc}"), file=sys.stderr)
        return EXIT_USAGE

    session = LiveFrameSession(
        args.url,
        interval_s=args.interval,
        max_height=args.max_height,
        stall_timeout_s=args.stall_timeout,
    )

    def stop(*_args: object) -> None:
        session.cancel()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    duration_timer: threading.Timer | None = None
    if args.duration > 0:
        duration_timer = threading.Timer(args.duration, session.cancel)
        duration_timer.daemon = True
        duration_timer.start()

    present_count = 0
    absent_count = 0
    print(
        f"live-probe watch={args.url} interval={args.interval:g}s "
        f"duration={args.duration:g}s detect={'on' if detector else 'off'}",
        flush=True,
    )

    try:
        for frame in session.frames():
            health = session.health
            pts = "n/a" if frame.timestamp_s is None else f"{frame.timestamp_s:.3f}"
            received = (
                frame.received_at.isoformat(timespec="milliseconds")
                if frame.received_at
                else "n/a"
            )
            line = (
                f"seq={frame.index} pts={pts} epoch={frame.extra.get('source_epoch')} "
                f"received={received} {health.summary()}"
            )
            if detector is not None:
                result = detector.detect_frame(frame)
                verdict = "PRESENT" if result.present else "ABSENT"
                if result.present:
                    present_count += 1
                else:
                    absent_count += 1
                line += f" scoreboard={verdict} conf={result.score:+.3f}"
            if args.print_every > 0 and frame.index % args.print_every == 0:
                print(line, flush=True)
    except SessionFailed as exc:
        print(f"SESSION FAILED: {sanitize_text(str(exc))}", file=sys.stderr)
        _print_summary(session, present_count, absent_count, detector is not None)
        return EXIT_FAILED
    except SessionError as exc:
        print(f"SESSION ERROR: {sanitize_text(str(exc))}", file=sys.stderr)
        _print_summary(session, present_count, absent_count, detector is not None)
        return EXIT_FAILED
    finally:
        if duration_timer is not None:
            duration_timer.cancel()
        session.cancel()

    _print_summary(session, present_count, absent_count, detector is not None)
    return EXIT_OK if session.state != SessionState.FAILED else EXIT_FAILED


def _print_summary(
    session: LiveFrameSession,
    present_count: int,
    absent_count: int,
    detected: bool,
) -> None:
    health = session.health
    ended = health.ended_at or datetime.now(UTC)
    duration = (ended - health.started_at).total_seconds()
    print("--- live-probe summary ---", flush=True)
    print(health.summary(), flush=True)
    print(
        f"duration_s={duration:.1f} interval_s={health.interval_s:g} "
        f"final_state={health.state.value}",
        flush=True,
    )
    if detected:
        print(
            f"detector PRESENT={present_count} ABSENT={absent_count} "
            "(single-frame labels, not a production accuracy estimate)",
            flush=True,
        )
    print(
        "Broadcast ended does not mean any game score is final. "
        "This session only ends acquisition.",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
