"""Redact YouTube Data API credentials from logs and stored errors."""

from __future__ import annotations

import re

from lsgc.live.sanitize import sanitize_text

_ASSIGNED_SECRET = re.compile(
    r"(?i)((?:[?&]|\b)(?:key|access_token|refresh_token|token)=)[^\s&'\"<>]+"
)
_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_HEADER_SECRET = re.compile(
    r"(?i)((?:authorization|x-goog-api-key)\s*[:=]\s*)\S+"
)


def sanitize_control_plane_text(text: str | None) -> str | None:
    """Redact API keys, OAuth tokens, and signed media URLs."""
    if text is None:
        return None
    redacted = sanitize_text(text) or ""
    redacted = _ASSIGNED_SECRET.sub(r"\1<redacted>", redacted)
    redacted = _BEARER.sub(r"\1<redacted>", redacted)
    redacted = _HEADER_SECRET.sub(r"\1<redacted>", redacted)
    return redacted
