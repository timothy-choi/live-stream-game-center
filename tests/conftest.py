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
