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
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime

import certifi
import numpy as np

from lsgc.errors import DecodeError, SourceResolutionError
from lsgc.models import Frame, MediaSource

_SHOWINFO = re.compile(r"n:\s*(\d+).*?pts_time:\s*(-?[\d.]+)")

_MAX_FRAGMENTS = 40
"""Hard cap on segments fetched per call, so a large `count` or `interval`
can never turn a bounded sample into a full-video download."""

_SEEK_BACKOFF_S = 2.0
"""FFmpeg's input seek lands on a keyframe, which may be past the requested
offset. Aiming slightly early lets the `select` filter land on it exactly."""

# The macOS python.org build does not trust system roots; the same reason
# certifi is a declared dependency.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


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


def fetch_fragment(source: MediaSource, index: int, *, timeout_s: float = 30.0) -> bytes:
    """Download one DASH segment.

    A fetch failure here means YouTube would not hand over the media, which is
    an acquisition problem rather than a decoding one - hence
    `SourceResolutionError`. Expired segment URLs surface as HTTP 403.
    """
    request = urllib.request.Request(
        source.fragment_urls[index], headers=dict(source.http_headers)
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s, context=_SSL_CONTEXT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise SourceResolutionError(
            f"Fragment {index} rejected by YouTube: HTTP {exc.code} {exc.reason}."
        ) from exc
    except Exception as exc:
        raise SourceResolutionError(f"Fragment {index} could not be fetched: {exc}") from exc


def probe_media(source: MediaSource, *, timeout_s: float = 30.0) -> dict:
    """Read authoritative stream properties with ffprobe.

    The resolver's declared width/height come from YouTube metadata; the raw
    pipe needs the real decoded geometry or every frame would be misaligned.
    For fragmented sources the base URL is not decodable, so we probe the
    first segment instead.
    """
    probe_input = "pipe:0" if source.is_fragmented else source.media_url
    payload_bytes = fetch_fragment(source, 0) if source.is_fragmented else None

    cmd = [
        _require("ffprobe"),
        "-hide_banner",
        "-loglevel", "error",
        *([] if source.is_fragmented else _header_args(source)),
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,codec_name:format=duration",
        "-of", "json",
        probe_input,
    ]
    try:
        result = subprocess.run(
            cmd, input=payload_bytes, capture_output=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        raise DecodeError(f"ffprobe timed out after {timeout_s}s.") from exc

    result_stdout = result.stdout.decode("utf-8", "replace")
    if result.returncode != 0:
        raise DecodeError(
            f"ffprobe failed ({result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )

    try:
        payload = json.loads(result_stdout)
        stream = payload["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise DecodeError(f"ffprobe returned no video stream: {result_stdout!r}") from exc

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


def _sample_filter(start_s: float, interval_s: float) -> str:
    """Build the FFmpeg filter that picks the frames we want.

    `select` is used rather than the `fps` filter because `fps` resamples onto
    a fixed grid and restamps frames with grid times - it will happily report a
    frame as being at 297s when 297.5s was requested. `select` passes through
    original frames with their true timestamps: take the first frame at or
    after `start_s`, then the next frame at least `interval_s` after whichever
    frame was last selected.
    """
    gate = f"gte(t\\,{start_s})"
    if interval_s > 0:
        gate += f"*(isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval_s}))"
    return f"select='{gate}',format=bgr24,showinfo"


def _fragment_range(
    source: MediaSource, start_s: float, count: int, interval_s: float
) -> range:
    """Segment indices that can cover the requested samples.

    Segments are self-contained, so we can begin mid-recording instead of
    decoding from the start. Index `n` is assumed to hold roughly
    `[n*duration, (n+1)*duration)`, which is only exact when the source
    timeline starts at zero; we begin one segment early so a stream whose
    timeline is offset still yields the requested instant rather than
    overshooting to the next segment boundary. The `select` filter drops the
    extra leading frames. The span is hard-capped so this stays a bounded
    sample rather than a download.
    """
    total = len(source.fragment_urls)
    duration = source.fragment_duration_s or 5.0
    span_end = start_s + max(0.0, interval_s) * max(0, count - 1)

    first = min(max(0, int(start_s // duration) - 1), max(0, total - 1))
    last = min(total - 1, int(span_end // duration) + 1)
    last = min(max(last, first), first + _MAX_FRAGMENTS - 1)
    return range(first, last + 1)


def _start_fragment_feeder(
    process: subprocess.Popen,
    source: MediaSource,
    indices: range,
    errors: list[Exception],
) -> threading.Thread:
    """Stream segments into FFmpeg's stdin, stopping as soon as it has enough.

    Feeding lazily means a short sample near the start of a long recording
    fetches only the segments it actually needs. When the reader has its frames
    and FFmpeg exits, the write fails with a broken pipe and we stop.
    """

    def feed() -> None:
        assert process.stdin is not None
        try:
            for index in indices:
                process.stdin.write(fetch_fragment(source, index))
                process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass  # FFmpeg already has what it needs.
        except SourceResolutionError as exc:
            errors.append(exc)
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    return thread


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
    if start_at_s is not None and (start_at_s < 0 or start_at_s != start_at_s):
        raise ValueError(f"start_at_s must be >= 0, got {start_at_s!r}")

    info = probe_media(source)
    width, height = info["width"], info["height"]
    frame_bytes = width * height * 3
    start = float(start_at_s or 0.0)

    cmd = [_require("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "info"]
    # Abort instead of blocking forever if the CDN stalls mid-read.
    cmd += ["-rw_timeout", "15000000"]
    # Keep source timestamps absolute so `select` below can address real
    # positions in the recording and Frame.timestamp_s survives seeking.
    cmd += ["-copyts"]

    if source.is_fragmented:
        fragment_range = _fragment_range(source, start, count, interval_s)
        cmd += ["-i", "pipe:0"]
        stdin = subprocess.PIPE
    else:
        cmd += _header_args(source)
        if start > 0:
            cmd += ["-ss", str(max(0.0, start - _SEEK_BACKOFF_S))]
        cmd += ["-i", source.media_url]
        fragment_range = range(0)
        stdin = subprocess.DEVNULL

    cmd += ["-an", "-sn", "-vf", _sample_filter(start, interval_s)]
    # -vsync 0 keeps the selected frames' own timestamps instead of forcing
    # them onto a constant-rate grid.
    cmd += ["-vsync", "0"]
    # Bound the work at the source: never ask ffmpeg for more than we sample.
    cmd += ["-frames:v", str(count), "-f", "rawvideo", "pipe:1"]

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=stdin
    )

    fetch_errors: list[Exception] = []
    if source.is_fragmented:
        _start_fragment_feeder(process, source, fragment_range, fetch_errors)

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
                    # Requested offset travels with the frame so a diagnostic
                    # sample can be reproduced exactly.
                    "requested_offset_s": start,
                },
            )
            delivered += 1
    finally:
        watchdog.cancel()
        _shutdown(process)
        reader.join(timeout=2.0)

    # A fragment that YouTube refused is an acquisition failure, and reporting
    # it as a decode error would point debugging at the wrong layer.
    if fetch_errors and delivered < count:
        raise fetch_errors[0]
    if timed_out.is_set() and delivered < count:
        raise DecodeError(
            f"Decoding timed out after {timeout_s}s with {delivered}/{count} frames. "
            + _tail(stderr_lines, lock)
        )
    if delivered == 0:
        raise DecodeError(
            f"FFmpeg produced no frames (requested offset {start}s; the source may be "
            f"shorter than that). " + _tail(stderr_lines, lock)
        )


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
