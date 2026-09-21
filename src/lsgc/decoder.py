"""Decoding and sampling: MediaSource -> bounded stream of `Frame`.

FFmpeg does the demuxing and decoding; we read raw BGR bytes off its stdout and
wrap them in numpy arrays. We never write the media to disk, and we stop the
process as soon as the requested number of samples is in hand, so a 10-hour
broadcast costs the same as a 10-second clip.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from datetime import UTC, datetime

import numpy as np

from lsgc.errors import DecodeError
from lsgc.models import Frame, MediaSource

_SHOWINFO = re.compile(r"n:\s*(\d+).*?pts_time:\s*(-?[\d.]+)")


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise DecodeError(f"{tool} not found on PATH. Install FFmpeg.")
    return path


def _header_args(source: MediaSource) -> list[str]:
    """Replay the resolver's headers.

    Google's CDN binds media URLs to the client that requested them, so a URL
    fetched by yt-dlp under one user-agent can 403 under ffmpeg's default one.
    """
    args: list[str] = []
    headers = {k: v for k, v in source.http_headers.items() if v}
    user_agent = headers.pop("User-Agent", None)
    if user_agent:
        args += ["-user_agent", user_agent]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    return args


def probe_media(source: MediaSource, *, timeout_s: float = 30.0) -> dict:
    """Read authoritative stream properties with ffprobe.

    The resolver's declared width/height come from YouTube metadata; the raw
    pipe needs the real decoded geometry or every frame would be misaligned.
    """
    cmd = [
        _require("ffprobe"),
        "-hide_banner",
        "-loglevel", "error",
        *_header_args(source),
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,codec_name:format=duration",
        "-of", "json",
        source.media_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_s, text=True)
    except subprocess.TimeoutExpired as exc:
        raise DecodeError(f"ffprobe timed out after {timeout_s}s.") from exc

    if result.returncode != 0:
        raise DecodeError(f"ffprobe failed ({result.returncode}): {result.stderr.strip()}")

    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise DecodeError(f"ffprobe returned no video stream: {result.stdout!r}") from exc

    width, height = int(stream["width"]), int(stream["height"])
    if width <= 0 or height <= 0:
        raise DecodeError(f"ffprobe reported unusable dimensions {width}x{height}.")

    return {
        "width": width,
        "height": height,
        "codec": stream.get("codec_name"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "duration_s": _to_float(payload.get("format", {}).get("duration")),
    }


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iter_frames(
    source: MediaSource,
    *,
    count: int = 5,
    interval_s: float = 1.0,
    start_at_s: float | None = None,
    timeout_s: float = 90.0,
) -> Iterator[Frame]:
    """Yield at most `count` frames sampled `interval_s` apart.

    Set `interval_s` to 0 to take consecutive decoded frames.
    """
    if count < 1:
        raise ValueError("count must be >= 1")

    info = probe_media(source)
    width, height = info["width"], info["height"]
    frame_bytes = width * height * 3

    filters = [] if interval_s <= 0 else [f"fps=1/{interval_s}"]
    filters += ["format=bgr24", "showinfo"]

    cmd = [_require("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "info"]
    # Abort instead of blocking forever if the CDN stalls mid-read.
    cmd += ["-rw_timeout", "15000000"]
    cmd += _header_args(source)
    if start_at_s:
        cmd += ["-ss", str(start_at_s)]
    cmd += ["-i", source.media_url, "-an", "-sn"]
    cmd += ["-vf", ",".join(filters)]
    # Bound the work at the source: never ask ffmpeg for more than we sample.
    cmd += ["-frames:v", str(count), "-f", "rawvideo", "pipe:1"]

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
    )

    timestamps: dict[int, float] = {}
    stderr_lines: list[str] = []
    lock = threading.Lock()

    def drain_stderr() -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            with lock:
                stderr_lines.append(line)
                if len(stderr_lines) > 200:
                    del stderr_lines[:100]
                if (match := _SHOWINFO.search(line)) and "showinfo" in line:
                    timestamps[int(match.group(1))] = float(match.group(2))

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    # Hard deadline. A blocking pipe read cannot be interrupted, so the only
    # reliable way to guarantee termination is to kill the child.
    timed_out = threading.Event()

    def on_timeout() -> None:
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(timeout_s, on_timeout)
    watchdog.start()

    delivered = 0
    try:
        assert process.stdout is not None
        while delivered < count:
            buffer = process.stdout.read(frame_bytes)
            if not buffer or len(buffer) < frame_bytes:
                break

            image = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3).copy()
            yield Frame(
                index=delivered,
                image=image,
                width=width,
                height=height,
                timestamp_s=_await_timestamp(timestamps, delivered, lock),
                source_url=source.source_url,
                source_id=source.source_id,
                is_live=source.is_live,
                received_at=datetime.now(UTC),
                extra={
                    "codec": info["codec"],
                    "avg_frame_rate": info["avg_frame_rate"],
                    "format_id": source.format_id,
                    "protocol": source.protocol,
                },
            )
            delivered += 1
    finally:
        watchdog.cancel()
        _shutdown(process)
        reader.join(timeout=2.0)

    if timed_out.is_set() and delivered < count:
        raise DecodeError(
            f"Decoding timed out after {timeout_s}s with {delivered}/{count} frames. "
            + _tail(stderr_lines, lock)
        )
    if delivered == 0:
        raise DecodeError("FFmpeg produced no frames. " + _tail(stderr_lines, lock))


def _await_timestamp(
    timestamps: dict[int, float], index: int, lock: threading.Lock
) -> float | None:
    """showinfo reaches us on stderr, which can lag the frame data on stdout."""
    for _ in range(20):
        with lock:
            if index in timestamps:
                return timestamps[index]
        threading.Event().wait(0.02)
    return None


def _shutdown(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for pipe in (process.stdout, process.stderr):
        if pipe:
            pipe.close()


def _tail(lines: list[str], lock: threading.Lock, limit: int = 8) -> str:
    with lock:
        relevant = [ln for ln in lines if ln and "showinfo" not in ln][-limit:]
    return "FFmpeg output: " + " | ".join(relevant) if relevant else "No FFmpeg output."
