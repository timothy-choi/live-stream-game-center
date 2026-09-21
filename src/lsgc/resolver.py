"""Source resolution: YouTube URL -> playable media URL.

This is the only module that knows yt-dlp exists. Swapping resolvers, or adding
non-YouTube sources later, means adding a function that returns a `MediaSource`.
"""

from __future__ import annotations

from typing import Any

from lsgc.errors import SourceResolutionError
from lsgc.models import MediaSource

_DEFAULT_MAX_HEIGHT = 720
"""Scoreboard OCR does not need source resolution, and smaller frames cost less
bandwidth per tracked broadcast."""


def _select_format(info: dict[str, Any], max_height: int) -> dict[str, Any]:
    """Pick the best video-bearing format at or below `max_height`.

    Formats missing a URL are skipped: YouTube omits the URL for formats it
    only serves over SABR, and those cannot be handed to ffmpeg.
    """
    formats = info.get("formats") or []
    candidates = [
        f
        for f in formats
        if f.get("url")
        and f.get("vcodec", "none") != "none"
        and (f.get("height") or 0) <= max_height
    ]
    if not candidates:
        raise SourceResolutionError(
            f"No playable video format at or below {max_height}p. "
            f"{len(formats)} formats were listed but none had a usable URL "
            "(YouTube may be forcing SABR-only delivery for this video)."
        )
    return max(candidates, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))


def resolve_youtube(
    url: str,
    *,
    max_height: int = _DEFAULT_MAX_HEIGHT,
    socket_timeout: float = 20.0,
) -> MediaSource:
    """Resolve a YouTube URL into a `MediaSource`.

    Raises `SourceResolutionError` for anything that prevents us from getting a
    playable URL: bad input, unavailable video, or YouTube refusing access.
    """
    if not url or not url.strip():
        raise SourceResolutionError("No URL provided.")

    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, ExtractorError
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SourceResolutionError(f"yt-dlp is not installed: {exc}") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": socket_timeout,
        # Live manifests must not be collapsed into a single downloadable file;
        # we want the HLS URL so we can read the live edge.
        "live_from_start": False,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as exc:
        raise SourceResolutionError(f"yt-dlp could not resolve {url!r}: {exc}") from exc
    except Exception as exc:  # network stack, DNS, unexpected extractor state
        raise SourceResolutionError(f"Unexpected error resolving {url!r}: {exc}") from exc

    if not info:
        raise SourceResolutionError(f"yt-dlp returned no metadata for {url!r}.")
    if info.get("_type") == "playlist":
        raise SourceResolutionError(
            f"{url!r} resolved to a playlist/channel. Provide a single video URL."
        )

    selected = _select_format(info, max_height)
    fragments = tuple(
        f["url"] for f in (selected.get("fragments") or []) if f.get("url")
    )

    return MediaSource(
        source_url=url,
        media_url=selected["url"],
        is_live=bool(info.get("is_live")),
        source_id=info.get("id"),
        title=info.get("title"),
        duration_s=info.get("duration"),
        width=selected.get("width"),
        height=selected.get("height"),
        format_id=selected.get("format_id"),
        protocol=selected.get("protocol"),
        extractor=info.get("extractor"),
        http_headers=dict(selected.get("http_headers") or info.get("http_headers") or {}),
        fragment_urls=fragments,
        fragment_duration_s=_to_float(selected.get("target_duration")),
    )


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
