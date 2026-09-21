from __future__ import annotations

import pytest

from lsgc.errors import SourceResolutionError
from lsgc.resolver import _select_format, resolve_youtube


def test_blank_url_fails_before_any_network_call() -> None:
    with pytest.raises(SourceResolutionError, match="No URL"):
        resolve_youtube("   ")


def test_select_format_prefers_tallest_within_limit() -> None:
    info = {
        "formats": [
            {"url": "u360", "vcodec": "avc1", "height": 360, "format_id": "a"},
            {"url": "u720", "vcodec": "avc1", "height": 720, "format_id": "b"},
            {"url": "u1080", "vcodec": "avc1", "height": 1080, "format_id": "c"},
        ]
    }
    assert _select_format(info, 720)["format_id"] == "b"


def test_select_format_skips_audio_only_and_urlless_formats() -> None:
    """YouTube lists SABR-only formats without a URL; ffmpeg cannot use them."""
    info = {
        "formats": [
            {"url": "audio", "vcodec": "none", "height": 0, "format_id": "audio"},
            {"url": None, "vcodec": "avc1", "height": 720, "format_id": "sabr"},
            {"url": "ok", "vcodec": "avc1", "height": 480, "format_id": "usable"},
        ]
    }
    assert _select_format(info, 720)["format_id"] == "usable"


def test_select_format_reports_sabr_style_starvation() -> None:
    info = {"formats": [{"url": None, "vcodec": "avc1", "height": 720}]}
    with pytest.raises(SourceResolutionError, match="SABR"):
        _select_format(info, 720)


def test_select_format_with_no_formats() -> None:
    with pytest.raises(SourceResolutionError):
        _select_format({"formats": []}, 720)
