"""Redact resolved media URLs and other secrets from diagnostic text.

Session errors often wrap FFmpeg or yt-dlp messages that include the
googlevideo CDN URL, complete with signature and expiry. Those must never
reach logs, CLI output, or `SessionHealth.last_error`. Stable watch URLs
and video IDs are safe and are left intact.
"""

from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s'\"<>\\]+", re.IGNORECASE)

_UNSAFE_HOST_MARKERS = (
    "googlevideo.com",
    "googleusercontent.com",
    "youtube.com/api/",
    "youtube.com/videoplayback",
    "/videoplayback",
)

_UNSAFE_QUERY_MARKERS = (
    "signature=",
    "sig=",
    "lsig=",
    "expire=",
    "pot=",
)

_COOKIE_LINE = re.compile(
    r"(?i)(cookie|authorization|x-goog-|sapisid)[^\\n]{0,500}"
)


def is_unsafe_url(url: str) -> bool:
    lower = url.lower()
    if any(marker in lower for marker in _UNSAFE_HOST_MARKERS):
        return True
    if any(marker in lower for marker in _UNSAFE_QUERY_MARKERS):
        return True
    return False


def sanitize_text(text: str | None) -> str | None:
    """Return `text` with resolved media URLs and cookie-like headers redacted."""
    if text is None:
        return None
    redacted = _URL.sub(
        lambda match: "<redacted-media-url>" if is_unsafe_url(match.group(0)) else match.group(0),
        text,
    )
    redacted = _COOKIE_LINE.sub("<redacted-header>", redacted)
    return redacted


def looks_like_broadcast_ended(message: str) -> bool:
    """True when a resolver error is strong evidence the broadcast is over.

    yt-dlp wording varies; this is a conservative match list. Ambiguous
    failures (HTTP 403, timeouts) are treated as recoverable instead.
    """
    lower = message.lower()
    return any(
        marker in lower
        for marker in (
            "live event has ended",
            "live event is no longer live",
            "is no longer live",
            "channel is not currently live",
            "video is no longer available",
            "video unavailable",
            "this video has been removed",
            "private video",
        )
    )
