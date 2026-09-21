"""Bounded feasibility probe: resolve a YouTube URL and print frame evidence.

    python -m lsgc.probe "<youtube-url>" --frames 5

Exit codes are distinct so scripted checks can tell acquisition failures (2)
from decoder failures (3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lsgc.decoder import iter_frames
from lsgc.errors import DecodeError, SourceResolutionError
from lsgc.models import Frame
from lsgc.resolver import resolve_youtube

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_RESOLUTION = 2
EXIT_DECODE = 3

ARTIFACT_DIR = Path("artifacts/frames")


def _describe(frame: Frame) -> str:
    image = frame.image
    timestamp = "n/a" if frame.timestamp_s is None else f"{frame.timestamp_s:.3f}s"
    received = frame.received_at.isoformat(timespec="milliseconds") if frame.received_at else "n/a"
    return (
        f"  frame[{frame.index}] {frame.width}x{frame.height} "
        f"pts={timestamp} received={received}\n"
        f"    array: shape={image.shape} dtype={image.dtype} "
        f"contiguous={image.flags['C_CONTIGUOUS']} "
        f"mean={image.mean():.2f} min={image.min()} max={image.max()}"
    )


def _save(frame: Frame, source_id: str | None, label: str | None = None) -> Path:
    """Write a diagnostic frame under the gitignored artifacts directory.

    The video id and source offset are in the filename so a sample can be
    reproduced later; without the offset, runs at different offsets would
    overwrite each other.
    """
    import cv2

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    offset = frame.timestamp_s
    stamp = "unknown" if offset is None else f"{offset:09.3f}"
    name = f"{label + '_' if label else ''}{source_id or 'frame'}_t{stamp}s.png"
    path = ARTIFACT_DIR / name
    if not cv2.imwrite(str(path), frame.image):
        raise DecodeError(f"cv2.imwrite failed for {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.probe",
        description="Decode a bounded number of frames from a YouTube source.",
    )
    parser.add_argument("url", help="YouTube video or livestream URL")
    parser.add_argument("--frames", type=int, default=5, help="samples to decode (default 5)")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="seconds between samples, 0 for consecutive"
    )
    parser.add_argument("--max-height", type=int, default=720, help="max source height")
    parser.add_argument("--start-at", type=float, default=None, help="seek offset, archived only")
    parser.add_argument("--timeout", type=float, default=90.0, help="decode deadline in seconds")
    parser.add_argument("--save", type=int, default=0, help="write the first N frames as PNG")
    parser.add_argument("--label", default=None, help="prefix for saved diagnostic filenames")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    print(f"[1/3] Resolving source: {args.url}")
    try:
        source = resolve_youtube(args.url, max_height=args.max_height)
    except SourceResolutionError as exc:
        print(f"ACQUISITION FAILED: {exc}", file=sys.stderr)
        return EXIT_RESOLUTION

    kind = "LIVE" if source.is_live else "ARCHIVED"
    delivery = (
        f"{len(source.fragment_urls)} DASH segments @ ~{source.fragment_duration_s}s"
        if source.is_fragmented
        else "single playable URL"
    )
    print(
        f"      {kind} id={source.source_id} title={source.title!r}\n"
        f"      format={source.format_id} protocol={source.protocol} "
        f"declared={source.width}x{source.height} duration={source.duration_s}\n"
        f"      delivery={delivery}"
    )

    print(
        f"[2/3] Decoding up to {args.frames} frames "
        f"(start_at={args.start_at}, interval={args.interval}s)"
    )
    frames: list[Frame] = []
    saved: list[Path] = []
    try:
        for frame in iter_frames(
            source,
            count=args.frames,
            interval_s=args.interval,
            start_at_s=args.start_at,
            timeout_s=args.timeout,
        ):
            print(_describe(frame))
            if len(saved) < args.save:
                saved.append(_save(frame, source.source_id, args.label))
            frames.append(frame)
    except SourceResolutionError as exc:
        print(f"ACQUISITION FAILED after {len(frames)} frames: {exc}", file=sys.stderr)
        return EXIT_RESOLUTION
    except DecodeError as exc:
        print(f"DECODE FAILED after {len(frames)} frames: {exc}", file=sys.stderr)
        return EXIT_DECODE
    except ValueError as exc:
        print(f"INVALID ARGUMENT: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(f"[3/3] Decoded {len(frames)}/{args.frames} samples from a {kind} source.")
    if saved:
        print("      Saved diagnostics: " + ", ".join(str(p) for p in saved))

    timestamps = [f.timestamp_s for f in frames if f.timestamp_s is not None]
    if timestamps:
        print(f"      Timestamp range: {min(timestamps):.3f}s .. {max(timestamps):.3f}s")
        if args.start_at is not None:
            print(
                f"      Seek: requested {args.start_at:.3f}s, "
                f"first frame {timestamps[0]:.3f}s, "
                f"error {timestamps[0] - args.start_at:+.3f}s"
            )
    else:
        print("      No decoder timestamps were reported for this source.")

    return EXIT_OK if len(frames) == args.frames else EXIT_DECODE


if __name__ == "__main__":
    raise SystemExit(main())
