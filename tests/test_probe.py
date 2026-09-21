from __future__ import annotations

import pytest

from lsgc import probe
from lsgc.errors import DecodeError, SourceResolutionError


def test_defaults() -> None:
    args = probe.build_parser().parse_args(["https://youtu.be/x"])
    assert args.frames == 5
    assert args.interval == 1.0
    assert args.save == 0


def test_frames_flag() -> None:
    args = probe.build_parser().parse_args(["https://youtu.be/x", "--frames", "3"])
    assert args.frames == 3


def test_url_is_required() -> None:
    with pytest.raises(SystemExit):
        probe.build_parser().parse_args([])


def test_acquisition_failure_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise SourceResolutionError("video unavailable")

    monkeypatch.setattr(probe, "resolve_youtube", boom)
    assert probe.main(["https://youtu.be/bad"]) == probe.EXIT_RESOLUTION


def test_decode_failure_exit_code(monkeypatch: pytest.MonkeyPatch, local_source) -> None:
    """A decoder failure must not look like an acquisition failure."""

    def fake_resolve(*args, **kwargs):
        return local_source

    def boom(*args, **kwargs):
        raise DecodeError("no frames")
        yield  # pragma: no cover - keeps this a generator

    monkeypatch.setattr(probe, "resolve_youtube", fake_resolve)
    monkeypatch.setattr(probe, "iter_frames", boom)
    assert probe.main(["https://youtu.be/x"]) == probe.EXIT_DECODE


def test_success_path_against_local_clip(monkeypatch: pytest.MonkeyPatch, local_source) -> None:
    monkeypatch.setattr(probe, "resolve_youtube", lambda *a, **k: local_source)
    assert probe.main(["file://clip", "--frames", "2", "--interval", "1"]) == probe.EXIT_OK
