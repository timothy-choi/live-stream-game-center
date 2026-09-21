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


FIXTURE_DIR = Path(__file__).parent / "fixtures"
M1_PROFILE_PATH = FIXTURE_DIR / "m1_fixture_profile.json"
"""Profile calibrated from the M1 Madden diagnostic frames.

Fixture data. Its channel, geometry, and logo-only identity describe one
capture setup and are never product defaults.
"""


@pytest.fixture
def valid_profile():
    """A minimal well-formed profile, independent of the M1 fixture.

    Deliberately synthetic so validation tests do not quietly depend on real
    Madden geometry.
    """
    from datetime import UTC, datetime

    from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region

    return CalibrationProfile(
        profile_id="test-profile",
        channel_id="UC_test_channel",
        revision=1,
        reference_width=1280,
        reference_height=720,
        scoreboard=Region(300, 60, 600, 80),
        left_score=Region(400, 80, 40, 30),
        right_score=Region(760, 80, 40, 30),
        left_identity=Region(320, 70, 60, 60),
        right_identity=Region(820, 70, 60, 60),
        left_identity_presentation=IdentityPresentation.LOGO,
        right_identity_presentation=IdentityPresentation.LOGO,
        stable_anchors=(Region(500, 70, 20, 40),),
        dynamic_ignore=(Region(600, 80, 50, 20),),
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def synthetic_frame() -> "np.ndarray":  # noqa: F821
    """A 1280x720 BGR image with structure, so overlay drawing is detectable."""
    import numpy as np

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[:, :, 0] = 40
    image[100:200, 200:600] = (80, 90, 100)
    return image


M3_MANIFEST_PATH = FIXTURE_DIR / "m3_validation_set.json"
M3_REFERENCE_PATH = FIXTURE_DIR / "m3_reference.json"
"""Labelled samples and reference evidence from the M1 Madden recording.

Metadata only. The decoded frames they describe stay under gitignored
artifacts, so tests that need imagery synthesise it instead.
"""


@pytest.fixture
def detection_profile():
    """A synthetic two-anchor profile, independent of the Madden fixture."""
    from datetime import UTC, datetime

    from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region

    return CalibrationProfile(
        profile_id="detect-test",
        channel_id="UC_detect_channel",
        revision=1,
        reference_width=640,
        reference_height=360,
        scoreboard=Region(100, 40, 440, 60),
        left_score=Region(180, 55, 40, 30),
        right_score=Region(420, 55, 40, 30),
        left_identity=Region(110, 45, 60, 50),
        right_identity=Region(470, 45, 60, 50),
        left_identity_presentation=IdentityPresentation.LOGO,
        right_identity_presentation=IdentityPresentation.LOGO,
        stable_anchors=(Region(240, 48, 24, 44), Region(370, 48, 24, 44)),
        dynamic_ignore=(Region(300, 55, 40, 30),),
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def _draw_anchor_structure(image, region, flip: bool = False) -> None:
    """Put a deterministic, high-contrast pattern inside a region.

    Detection compares structure, so a fixture image needs real structure:
    a flat patch correlates with nothing and would make every test score zero.
    """
    import cv2

    for step in range(0, region.height, 6):
        y = region.y + step
        offset = (region.height - step) if flip else step
        x = region.x + (offset % max(1, region.width))
        cv2.line(image, (region.x, y), (min(x, region.right - 1), y), (255, 255, 255), 2)


@pytest.fixture
def scoreboard_image(detection_profile):
    """A frame whose anchors carry the structure a reference is built from."""
    import numpy as np

    image = np.full((360, 640, 3), 30, dtype=np.uint8)
    image[40:100, 100:540] = (70, 60, 50)
    for anchor in detection_profile.stable_anchors:
        _draw_anchor_structure(image, anchor)
    return image


@pytest.fixture
def imposter_image(detection_profile):
    """Same size and band, different structure: the synthetic hard negative."""
    import numpy as np

    image = np.full((360, 640, 3), 30, dtype=np.uint8)
    image[40:100, 100:540] = (50, 60, 70)
    for anchor in detection_profile.stable_anchors:
        _draw_anchor_structure(image, anchor, flip=True)
    return image


@pytest.fixture
def scoreboard_reference(detection_profile, scoreboard_image):
    from lsgc.vision import build_reference

    return build_reference([scoreboard_image], detection_profile)


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
