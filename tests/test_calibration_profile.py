"""Profile validation, channel binding, identity presentation and revisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lsgc.calibration import (
    CalibrationProfile,
    CalibrationValidationError,
    ChannelMismatchError,
    IdentityPresentation,
    Region,
)


class TestValidProfile:
    def test_accepts_a_well_formed_profile(self, valid_profile: CalibrationProfile) -> None:
        valid_profile.validate()
        assert valid_profile.revision == 1
        assert valid_profile.schema_version == 1

    def test_is_immutable(self, valid_profile: CalibrationProfile) -> None:
        with pytest.raises(Exception):
            valid_profile.revision = 2  # type: ignore[misc]


class TestRequiredMetadata:
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_channel_id_must_be_present(
        self, valid_profile: CalibrationProfile, blank: str
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="channel_id"):
            replace(valid_profile, channel_id=blank)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_profile_id_must_be_present(
        self, valid_profile: CalibrationProfile, blank: str
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="profile_id"):
            replace(valid_profile, profile_id=blank)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_revision_must_be_positive(
        self, valid_profile: CalibrationProfile, bad: int
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="revision"):
            replace(valid_profile, revision=bad)

    @pytest.mark.parametrize(
        ("width", "height"), [(0, 720), (1280, 0), (-1280, 720)]
    )
    def test_reference_frame_must_be_positive(
        self, valid_profile: CalibrationProfile, width: int, height: int
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="reference frame"):
            replace(valid_profile, reference_width=width, reference_height=height)

    def test_created_at_must_be_timezone_aware(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="timezone-aware"):
            replace(valid_profile, created_at=datetime(2026, 1, 1, 12, 0))

    def test_unsupported_schema_version_rejected(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="schema_version"):
            replace(valid_profile, schema_version=99)


class TestGeometryValidation:
    def test_scoreboard_must_be_inside_the_reference_frame(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="scoreboard .* outside"):
            replace(valid_profile, scoreboard=Region(1000, 60, 600, 80))

    @pytest.mark.parametrize("field", ["left_score", "right_score"])
    def test_score_region_must_sit_inside_the_scoreboard(
        self, valid_profile: CalibrationProfile, field: str
    ) -> None:
        with pytest.raises(CalibrationValidationError, match=f"{field}.*not inside scoreboard"):
            replace(valid_profile, **{field: Region(50, 300, 40, 30)})

    @pytest.mark.parametrize("field", ["left_identity", "right_identity"])
    def test_identity_region_must_sit_inside_the_scoreboard(
        self, valid_profile: CalibrationProfile, field: str
    ) -> None:
        with pytest.raises(CalibrationValidationError, match=f"{field}.*not inside scoreboard"):
            replace(valid_profile, **{field: Region(50, 300, 60, 60)})

    def test_region_partially_overlapping_the_scoreboard_is_rejected(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Straddling the boundary is a mis-calibration, not a near miss."""
        with pytest.raises(CalibrationValidationError, match="not inside scoreboard"):
            replace(valid_profile, left_score=Region(880, 80, 40, 30))

    def test_reports_every_problem_at_once(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError) as info:
            replace(
                valid_profile,
                left_score=Region(50, 300, 40, 30),
                right_identity=Region(60, 320, 40, 30),
            )
        assert "left_score" in str(info.value)
        assert "right_identity" in str(info.value)


class TestStableAnchors:
    def test_at_least_one_anchor_is_required(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="stable anchor"):
            replace(valid_profile, stable_anchors=())

    def test_anchors_must_sit_inside_the_scoreboard(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match=r"stable_anchors\[0\]"):
            replace(valid_profile, stable_anchors=(Region(10, 400, 20, 20),))

    def test_multiple_anchors_allowed(self, valid_profile: CalibrationProfile) -> None:
        updated = replace(
            valid_profile,
            stable_anchors=(Region(500, 70, 20, 40), Region(540, 70, 20, 40)),
        )
        assert len(updated.stable_anchors) == 2


class TestDynamicIgnoreRegions:
    def test_may_be_empty(self, valid_profile: CalibrationProfile) -> None:
        assert replace(valid_profile, dynamic_ignore=()).dynamic_ignore == ()

    def test_must_be_inside_the_reference_frame(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match=r"dynamic_ignore\[0\]"):
            replace(valid_profile, dynamic_ignore=(Region(1270, 715, 50, 50),))

    def test_may_sit_outside_the_scoreboard(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Only anchors are constrained to the board; a distraction elsewhere on
        screen is still worth ignoring, and forbidding it would encode an
        assumption one sample does not justify."""
        updated = replace(valid_profile, dynamic_ignore=(Region(10, 600, 100, 40),))
        assert updated.dynamic_ignore[0] == Region(10, 600, 100, 40)

    def test_may_coincide_with_score_regions(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Scores are dynamic, yet stay separately represented."""
        updated = replace(valid_profile, dynamic_ignore=(valid_profile.left_score,))
        assert updated.dynamic_ignore[0] == updated.left_score


class TestIdentityPresentation:
    def test_logo_only(self) -> None:
        assert IdentityPresentation.parse("logo").to_names() == ["logo"]

    def test_combination(self) -> None:
        combined = IdentityPresentation.parse("name,logo")
        assert combined.to_names() == ["logo", "name"]
        assert IdentityPresentation.NAME in combined
        assert IdentityPresentation.ABBREVIATION not in combined

    def test_all_three(self) -> None:
        every = IdentityPresentation.parse(["name", "abbreviation", "logo"])
        assert every.to_names() == ["abbreviation", "logo", "name"]

    def test_names_are_sorted_for_stable_output(self) -> None:
        assert (
            IdentityPresentation.parse("logo,name").to_names()
            == IdentityPresentation.parse("name,logo").to_names()
        )

    def test_empty_rejected(self) -> None:
        with pytest.raises(CalibrationValidationError):
            IdentityPresentation.parse("")

    def test_unknown_rejected(self) -> None:
        with pytest.raises(CalibrationValidationError, match="Unknown identity"):
            IdentityPresentation.parse("mascot")

    def test_profile_requires_non_empty_presentation(
        self, valid_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="left_identity_presentation"):
            replace(valid_profile, left_identity_presentation=IdentityPresentation(0))

    def test_logo_is_not_a_global_default(self) -> None:
        """Presentation is always supplied explicitly; there is no default."""
        import inspect

        signature = inspect.signature(CalibrationProfile)
        for side in ("left", "right"):
            parameter = signature.parameters[f"{side}_identity_presentation"]
            assert parameter.default is inspect.Parameter.empty


class TestChannelBinding:
    def test_matching_channel_passes(self, valid_profile: CalibrationProfile) -> None:
        valid_profile.assert_channel("UC_test_channel")
        assert valid_profile.matches_channel("UC_test_channel")

    def test_mismatched_channel_raises(self, valid_profile: CalibrationProfile) -> None:
        with pytest.raises(ChannelMismatchError) as info:
            valid_profile.assert_channel("UC_some_other_channel")
        assert "UC_test_channel" in str(info.value)
        assert "UC_some_other_channel" in str(info.value)

    def test_mismatch_is_detectable_without_raising(
        self, valid_profile: CalibrationProfile
    ) -> None:
        assert valid_profile.matches_channel("UC_other") is False

    def test_channel_comparison_is_exact(self, valid_profile: CalibrationProfile) -> None:
        for near_miss in ("uc_test_channel", "UC_test_channel ", "UC_test"):
            assert not valid_profile.matches_channel(near_miss)


class TestRevisions:
    def test_next_revision_increments_without_touching_the_original(
        self, valid_profile: CalibrationProfile
    ) -> None:
        second = valid_profile.next_revision(notes="board moved down 4px")
        assert (valid_profile.revision, second.revision) == (1, 2)
        assert valid_profile.notes == ""
        assert second.notes == "board moved down 4px"

    def test_revisions_can_carry_different_geometry(
        self, valid_profile: CalibrationProfile
    ) -> None:
        moved = Region(300, 64, 600, 80)
        second = valid_profile.next_revision(scoreboard=moved)
        assert valid_profile.scoreboard != second.scoreboard
        assert second.scoreboard == moved

    def test_revision_keeps_lineage_and_channel(
        self, valid_profile: CalibrationProfile
    ) -> None:
        second = valid_profile.next_revision()
        assert second.profile_id == valid_profile.profile_id
        assert second.channel_id == valid_profile.channel_id

    def test_schema_version_is_independent_of_revision(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Recalibrating many times must not change the format version."""
        profile = valid_profile
        for _ in range(5):
            profile = profile.next_revision()
        assert profile.revision == 6
        assert profile.schema_version == valid_profile.schema_version == 1

    def test_explicit_revision_number_is_respected(
        self, valid_profile: CalibrationProfile
    ) -> None:
        assert valid_profile.next_revision(revision=7).revision == 7


class TestFrameSizeScaling:
    def test_720p_profile_scales_to_1080p(self, valid_profile: CalibrationProfile) -> None:
        scaled = valid_profile.for_frame_size(1920, 1080)
        assert (scaled.reference_width, scaled.reference_height) == (1920, 1080)
        assert scaled.scoreboard == Region(450, 90, 900, 120)
        assert scaled.left_score == Region(600, 120, 60, 45)

    def test_scaled_profile_is_still_valid(self, valid_profile: CalibrationProfile) -> None:
        scaled = valid_profile.for_frame_size(1920, 1080)
        scaled.validate()
        assert scaled.scoreboard.contains(scaled.left_score)
        assert scaled.scoreboard.contains(scaled.stable_anchors[0])

    def test_identical_size_returns_the_same_profile(
        self, valid_profile: CalibrationProfile
    ) -> None:
        assert valid_profile.for_frame_size(1280, 720) is valid_profile

    @pytest.mark.parametrize("size", [(1280, 1024), (1440, 1080), (2560, 1080)])
    def test_incompatible_aspect_ratio_rejected(
        self, valid_profile: CalibrationProfile, size: tuple[int, int]
    ) -> None:
        with pytest.raises(CalibrationValidationError, match="aspect ratio"):
            valid_profile.for_frame_size(*size)

    def test_scales_to_youtubes_inexact_480p_rung(
        self, valid_profile: CalibrationProfile
    ) -> None:
        scaled = valid_profile.for_frame_size(854, 480)
        scaled.validate()
        assert scaled.scoreboard.fits_within(854, 480)

    def test_non_positive_target_rejected(self, valid_profile: CalibrationProfile) -> None:
        with pytest.raises(CalibrationValidationError, match="positive"):
            valid_profile.for_frame_size(0, 0)

    def test_scaling_preserves_identity_and_binding(
        self, valid_profile: CalibrationProfile
    ) -> None:
        scaled = valid_profile.for_frame_size(640, 360)
        assert scaled.channel_id == valid_profile.channel_id
        assert scaled.revision == valid_profile.revision
        assert scaled.left_identity_presentation == valid_profile.left_identity_presentation

    def test_downscale_keeps_all_regions_valid(
        self, valid_profile: CalibrationProfile
    ) -> None:
        scaled = valid_profile.for_frame_size(640, 360)
        scaled.validate()
        assert all(r.width >= 1 and r.height >= 1 for r in scaled.stable_anchors)


class TestProfileContainsNoOpponentIdentity:
    def test_no_team_or_score_or_timestamp_fields(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Calibration stores layout, never who was playing or when."""
        forbidden = ("team", "opponent", "score_value", "colour", "color",
                     "timestamp", "offset", "logo_image")
        fields = {f.name for f in valid_profile.__dataclass_fields__.values()}
        for name in fields:
            assert not any(word in name for word in forbidden), name

    def test_created_at_is_the_only_time_field(
        self, valid_profile: CalibrationProfile
    ) -> None:
        time_fields = [
            name for name in valid_profile.__dataclass_fields__ if "at" == name[-2:]
        ]
        assert time_fields == ["created_at"]
        assert valid_profile.created_at.tzinfo is UTC
