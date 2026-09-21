"""Decoder-layer tests against a synthetic local clip.

These prove the decode/sample/represent path. YouTube acquisition is verified
separately by running the CLI against a real URL.
"""

from __future__ import annotations

import numpy as np
import pytest

from lsgc.decoder import iter_frames, probe_media
from lsgc.errors import DecodeError
from lsgc.models import MediaSource


def test_probe_media_reports_geometry(local_source: MediaSource) -> None:
    info = probe_media(local_source)
    assert (info["width"], info["height"]) == (320, 240)
    assert info["codec"] == "h264"


def test_sampling_is_bounded(local_source: MediaSource) -> None:
    """A 6-second clip at 10fps holds 60 frames; we must stop at 3."""
    frames = list(iter_frames(local_source, count=3, interval_s=1.0))
    assert len(frames) == 3
    assert [f.index for f in frames] == [0, 1, 2]


def test_frames_are_opencv_compatible(local_source: MediaSource) -> None:
    frame = next(iter(iter_frames(local_source, count=1, interval_s=0)))

    assert isinstance(frame.image, np.ndarray)
    assert frame.image.dtype == np.uint8
    assert frame.image.shape == (240, 320, 3)
    assert frame.image.flags["C_CONTIGUOUS"]

    cv2 = pytest.importorskip("cv2")
    gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    assert gray.shape == (240, 320)
    # testsrc is a colour pattern, so a decoded frame cannot be uniform.
    assert frame.image.std() > 1.0


def test_metadata_is_populated(local_source: MediaSource) -> None:
    frame = next(iter(iter_frames(local_source, count=1, interval_s=0)))
    assert (frame.width, frame.height) == (320, 240)
    assert frame.source_url == local_source.source_url
    assert frame.source_id == "testsrc"
    assert frame.is_live is False
    assert frame.received_at is not None


def test_sample_interval_drives_timestamps(local_source: MediaSource) -> None:
    frames = list(iter_frames(local_source, count=3, interval_s=2.0))
    timestamps = [f.timestamp_s for f in frames]
    assert all(t is not None for t in timestamps)
    assert timestamps == sorted(timestamps)
    assert timestamps[-1] - timestamps[0] == pytest.approx(4.0, abs=0.5)


def test_missing_media_raises_decode_error(tmp_path) -> None:
    source = MediaSource(
        source_url="file://missing",
        media_url=str(tmp_path / "does-not-exist.mp4"),
        is_live=False,
    )
    with pytest.raises(DecodeError):
        list(iter_frames(source, count=1))


def test_count_must_be_positive(local_source: MediaSource) -> None:
    with pytest.raises(ValueError):
        list(iter_frames(local_source, count=0))
