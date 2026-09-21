from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lsgc.models import MediaSource


@pytest.fixture(scope="session")
def local_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A short synthetic clip, so decoder tests never touch the network.

    This exercises the decoder only. It says nothing about YouTube acquisition.
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    path = tmp_path_factory.mktemp("media") / "testsrc.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=6",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        timeout=60,
    )
    return path


@pytest.fixture
def local_source(local_clip: Path) -> MediaSource:
    return MediaSource(
        source_url=f"file://{local_clip}",
        media_url=str(local_clip),
        is_live=False,
        source_id="testsrc",
        title="synthetic",
    )


SEGMENT_DURATION_S = 2.0
SEGMENT_COUNT = 10


@pytest.fixture(scope="session")
def local_segments(tmp_path_factory: pytest.TempPathFactory) -> list[bytes]:
    """Self-contained media segments, mirroring how YouTube serves past streams.

    MPEG-TS segments carry absolute timestamps and decode standalone, which is
    the property the fragment path relies on. Kept in memory so tests exercise
    the same feed-bytes-to-FFmpeg path as the real decoder.
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    directory = tmp_path_factory.mktemp("segments")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size=320x240:rate=25:duration={int(SEGMENT_DURATION_S * SEGMENT_COUNT)}",
            "-pix_fmt", "yuv420p", "-g", "25",
            "-f", "segment", "-segment_time", str(SEGMENT_DURATION_S),
            "-segment_format", "mpegts", "-reset_timestamps", "0",
            str(directory / "s%03d.ts"),
        ],
        check=True,
        timeout=120,
    )
    return [p.read_bytes() for p in sorted(directory.glob("*.ts"))]


@pytest.fixture
def fragmented_source(local_segments: list[bytes]) -> MediaSource:
    """A fragmented source whose segments are served from memory, not the network."""
    return MediaSource(
        source_url="https://example.invalid/watch?v=fake",
        media_url="https://example.invalid/base",
        is_live=False,
        source_id="fragmented",
        fragment_urls=tuple(f"https://example.invalid/seg/{i}" for i in range(len(local_segments))),
        fragment_duration_s=SEGMENT_DURATION_S,
    )


@pytest.fixture
def served_fragments(
    monkeypatch: pytest.MonkeyPatch, local_segments: list[bytes]
) -> list[int]:
    """Serve fragments from memory and record which indices were fetched."""
    from lsgc import decoder

    requested: list[int] = []

    def fake_fetch(source, index, *, timeout_s=30.0):
        requested.append(index)
        return local_segments[index]

    monkeypatch.setattr(decoder, "fetch_fragment", fake_fetch)
    return requested
