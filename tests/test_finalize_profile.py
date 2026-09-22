"""Finalization profile serialization, channel binding, and geometry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lsgc.calibration.errors import ChannelMismatchError
from lsgc.calibration.geometry import Region
from lsgc.finalize.errors import FinalizationValidationError
from lsgc.finalize.profile import FinalizationProfile
from lsgc.finalize.storage import profile_from_dict, profile_to_dict
from tests.finalize_support import layout_a


def test_round_trip_and_channel_binding(tmp_path) -> None:
    image, profile, reference, _glyphs = layout_a()
    assert profile.channel_id == "UC_final_a"
    assert len(profile.stable_anchors) >= 2
    data = profile_to_dict(profile)
    assert data["kind"] == "finalization"
    restored = profile_from_dict(data)
    assert restored.left_final_score_region == profile.left_final_score_region
    with pytest.raises(ChannelMismatchError):
        restored.assert_channel("UC_other")


def test_score_regions_cannot_be_anchors() -> None:
    score = Region(360, 170, 72, 44)
    with pytest.raises(FinalizationValidationError):
        FinalizationProfile(
            profile_id="bad",
            channel_id="UC_x",
            revision=1,
            reference_width=1280,
            reference_height=720,
            completion_panel=Region(240, 80, 800, 540),
            stable_anchors=(score, Region(960, 90, 70, 48)),
            left_final_score_region=score,
            right_final_score_region=Region(850, 170, 72, 44),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_filesystem_lookup_loads_finalization_for_exact_channel(tmp_path) -> None:
    from lsgc.broadcast.calibration import FilesystemCalibrationLookup
    from lsgc.calibration import IdentityPresentation, save_profile
    from lsgc.score.glyphs import save_glyphs
    from lsgc.vision.reference import save_reference
    from lsgc.finalize.storage import save_finalization_profile
    from tests.conftest import valid_profile as _unused  # noqa: F401
    from datetime import UTC, datetime
    from lsgc.calibration import CalibrationProfile

    image, final_profile, final_reference, glyphs = layout_a()
    board = CalibrationProfile(
        profile_id="board",
        channel_id=final_profile.channel_id,
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
        stable_anchors=(Region(500, 70, 20, 40), Region(620, 70, 20, 40)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    from lsgc.vision import build_reference
    import numpy as np

    board_image = np.full((720, 1280, 3), 30, dtype=np.uint8)
    board_image[60:140, 300:900] = (70, 60, 50)
    board_ref = build_reference([board_image], board)
    directory = tmp_path / final_profile.channel_id
    save_profile(board, directory / "profile.json")
    save_reference(board_ref, directory / "reference.json")
    save_glyphs(glyphs, directory / "glyphs.json")
    save_finalization_profile(final_profile, directory / "finalization.json")
    save_reference(final_reference, directory / "final_reference.json")
    loaded = FilesystemCalibrationLookup(tmp_path).get(final_profile.channel_id)
    assert loaded is not None
    assert loaded.finalization is not None
    assert loaded.finalization.profile_id == final_profile.profile_id
    assert FilesystemCalibrationLookup(tmp_path).get("UC_other") is None


def test_rescale_same_aspect() -> None:
    _image, profile, _reference, _glyphs = layout_a()
    fitted = profile.for_frame_size(1920, 1080)
    assert fitted.reference_width == 1920
    assert fitted.completion_panel.width == round(profile.completion_panel.width * 1.5)
    with pytest.raises(FinalizationValidationError):
        profile.for_frame_size(1280, 800)
