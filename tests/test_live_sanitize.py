from __future__ import annotations

from lsgc.live.sanitize import is_unsafe_url, looks_like_broadcast_ended, sanitize_text


def test_watch_url_is_kept() -> None:
    text = "resolved https://www.youtube.com/watch?v=abc123"
    assert sanitize_text(text) == text


def test_googlevideo_url_is_redacted() -> None:
    raw = (
        "FFmpeg failed: https://rr1---sn-abc.googlevideo.com/videoplayback"
        "?expire=123&signature=secretvalue extra"
    )
    cleaned = sanitize_text(raw)
    assert cleaned is not None
    assert "googlevideo" not in cleaned
    assert "secretvalue" not in cleaned
    assert "<redacted-media-url>" in cleaned
    assert cleaned.endswith(" extra")


def test_signature_query_is_treated_as_unsafe() -> None:
    url = "https://example.invalid/path?signature=abcd"
    assert is_unsafe_url(url)
    assert "abcd" not in (sanitize_text(url) or "")


def test_cookie_header_is_redacted() -> None:
    cleaned = sanitize_text("Cookie: SAPISID=secret; other")
    assert cleaned is not None
    assert "secret" not in cleaned
    assert "<redacted-header>" in cleaned


def test_broadcast_ended_markers() -> None:
    assert looks_like_broadcast_ended("ERROR: This live event has ended.")
    assert looks_like_broadcast_ended("The channel is not currently live")
    assert not looks_like_broadcast_ended("HTTP 403 Forbidden")
    assert not looks_like_broadcast_ended("temporary timeout")
