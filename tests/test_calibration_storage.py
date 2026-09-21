"""Serialization round trips, schema-version handling and the M1 fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import M1_PROFILE_PATH

from lsgc.calibration import (
    SCHEMA_VERSION,
    CalibrationProfile,
    ChannelMismatchError,
    IdentityPresentation,
    ProfileSchemaError,
    Region,
    load_profile,
    load_profile_unchecked,
    profile_from_dict,
    profile_to_dict,
    save_profile,
)


class TestRoundTrip:
    def test_dict_round_trip_preserves_every_field(
        self, valid_profile: CalibrationProfile
    ) -> None:
        assert profile_from_dict(profile_to_dict(valid_profile)) == valid_profile

    def test_file_round_trip(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        path = save_profile(valid_profile, tmp_path / "profile.json")
        assert load_profile_unchecked(path) == valid_profile

    def test_creates_missing_parent_directories(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        path = save_profile(valid_profile, tmp_path / "a" / "b" / "profile.json")
        assert path.exists()

    def test_output_is_byte_stable(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        """Re-saving an unchanged profile must not produce a diff."""
        first = save_profile(valid_profile, tmp_path / "one.json").read_bytes()
        second = save_profile(valid_profile, tmp_path / "two.json").read_bytes()
        assert first == second

    def test_output_is_readable_json_with_trailing_newline(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        text = save_profile(valid_profile, tmp_path / "p.json").read_text()
        assert text.endswith("}\n")
        assert "\n  " in text  # indented, not one long line
        assert json.loads(text)["schema_version"] == SCHEMA_VERSION

    def test_schema_version_is_the_first_key(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """A reader should see the format version before anything else."""
        assert next(iter(profile_to_dict(valid_profile))) == "schema_version"

    def test_identity_presentation_serializes_as_names(
        self, valid_profile: CalibrationProfile
    ) -> None:
        data = profile_to_dict(valid_profile)
        assert data["left_identity_presentation"] == ["logo"]

    def test_combined_presentation_round_trips(
        self, valid_profile: CalibrationProfile
    ) -> None:
        from dataclasses import replace

        combined = replace(
            valid_profile,
            left_identity_presentation=IdentityPresentation.NAME | IdentityPresentation.LOGO,
        )
        restored = profile_from_dict(profile_to_dict(combined))
        assert restored.left_identity_presentation == combined.left_identity_presentation


class TestSchemaVersioning:
    def test_unsupported_version_fails_clearly(
        self, valid_profile: CalibrationProfile
    ) -> None:
        data = profile_to_dict(valid_profile)
        data["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(ProfileSchemaError, match="Unsupported profile schema_version"):
            profile_from_dict(data)

    def test_missing_version_fails_clearly(
        self, valid_profile: CalibrationProfile
    ) -> None:
        data = profile_to_dict(valid_profile)
        del data["schema_version"]
        with pytest.raises(ProfileSchemaError, match="missing schema_version"):
            profile_from_dict(data)

    def test_version_is_checked_before_anything_else(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """A future format must not be misread as a malformed current one."""
        data = {"schema_version": 999}
        with pytest.raises(ProfileSchemaError, match="Unsupported"):
            profile_from_dict(data)


class TestMalformedInput:
    def test_not_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(ProfileSchemaError, match="not valid JSON"):
            load_profile_unchecked(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ProfileSchemaError, match="Could not read profile"):
            load_profile_unchecked(tmp_path / "absent.json")

    def test_not_an_object(self) -> None:
        with pytest.raises(ProfileSchemaError, match="must be a JSON object"):
            profile_from_dict([1, 2, 3])

    def test_missing_required_fields(self, valid_profile: CalibrationProfile) -> None:
        data = profile_to_dict(valid_profile)
        del data["left_score"]
        del data["channel_id"]
        with pytest.raises(ProfileSchemaError, match="missing required fields") as info:
            profile_from_dict(data)
        assert "left_score" in str(info.value)
        assert "channel_id" in str(info.value)

    def test_malformed_region(self, valid_profile: CalibrationProfile) -> None:
        data = profile_to_dict(valid_profile)
        del data["scoreboard"]["width"]
        with pytest.raises(ProfileSchemaError, match="scoreboard is missing width"):
            profile_from_dict(data)

    def test_region_of_wrong_type(self, valid_profile: CalibrationProfile) -> None:
        data = profile_to_dict(valid_profile)
        data["left_score"] = [1, 2, 3, 4]
        with pytest.raises(ProfileSchemaError, match="left_score must be an object"):
            profile_from_dict(data)

    def test_bad_timestamp(self, valid_profile: CalibrationProfile) -> None:
        data = profile_to_dict(valid_profile)
        data["created_at"] = "last tuesday"
        with pytest.raises(ProfileSchemaError, match="created_at"):
            profile_from_dict(data)

    def test_anchors_must_be_a_list(self, valid_profile: CalibrationProfile) -> None:
        data = profile_to_dict(valid_profile)
        data["stable_anchors"] = {"x": 1}
        with pytest.raises(ProfileSchemaError, match="stable_anchors must be a list"):
            profile_from_dict(data)

    def test_invalid_geometry_is_caught_on_load(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """Validation runs on load, not only on authoring."""
        from lsgc.calibration import CalibrationValidationError

        data = profile_to_dict(valid_profile)
        data["left_score"] = {"x": 0, "y": 0, "width": 10, "height": 10}
        with pytest.raises(CalibrationValidationError, match="not inside scoreboard"):
            profile_from_dict(data)


class TestLoadTimeChannelBinding:
    def test_load_requires_the_expected_channel(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        path = save_profile(valid_profile, tmp_path / "p.json")
        loaded = load_profile(path, expect_channel="UC_test_channel")
        assert loaded == valid_profile

    def test_load_rejects_another_channel(
        self, valid_profile: CalibrationProfile, tmp_path: Path
    ) -> None:
        path = save_profile(valid_profile, tmp_path / "p.json")
        with pytest.raises(ChannelMismatchError):
            load_profile(path, expect_channel="UC_a_different_channel")

    def test_expected_channel_is_not_optional(self) -> None:
        """Runtime code cannot load a profile without naming a channel, so a
        profile cannot drift into being treated as a global default."""
        import inspect

        parameter = inspect.signature(load_profile).parameters["expect_channel"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

        with pytest.raises(TypeError):
            load_profile("unused.json")  # type: ignore[call-arg]


class TestM1FixtureProfile:
    """The committed profile calibrated from the real M1 Madden frames.

    Everything asserted here describes that one capture setup. None of it is a
    product default.
    """

    FIXTURE_CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"

    @pytest.fixture
    def m1_profile(self) -> CalibrationProfile:
        return load_profile(M1_PROFILE_PATH, expect_channel=self.FIXTURE_CHANNEL)

    def test_fixture_exists_and_validates(self, m1_profile: CalibrationProfile) -> None:
        m1_profile.validate()
        assert m1_profile.revision == 1
        assert m1_profile.schema_version == SCHEMA_VERSION

    def test_reference_resolution_is_the_measured_one(
        self, m1_profile: CalibrationProfile
    ) -> None:
        assert (m1_profile.reference_width, m1_profile.reference_height) == (1280, 720)

    def test_measured_geometry(self, m1_profile: CalibrationProfile) -> None:
        assert m1_profile.scoreboard == Region(357, 65, 576, 65)
        assert m1_profile.left_score == Region(470, 89, 59, 26)
        assert m1_profile.right_score == Region(753, 89, 60, 26)
        assert m1_profile.left_identity == Region(368, 68, 95, 59)
        assert m1_profile.right_identity == Region(838, 78, 66, 49)

    def test_identity_presentation_is_logo_only_for_this_fixture(
        self, m1_profile: CalibrationProfile
    ) -> None:
        """This broadcast's gameplay scoreboard shows logos and no usable text.
        That is an observation about this channel, not about Madden."""
        assert m1_profile.left_identity_presentation is IdentityPresentation.LOGO
        assert m1_profile.right_identity_presentation is IdentityPresentation.LOGO

    def test_anchors_are_the_two_panel_dividers(
        self, m1_profile: CalibrationProfile
    ) -> None:
        assert len(m1_profile.stable_anchors) == 2
        for anchor in m1_profile.stable_anchors:
            assert m1_profile.scoreboard.contains(anchor)

    def test_anchors_avoid_the_dynamic_regions(
        self, m1_profile: CalibrationProfile
    ) -> None:
        """An anchor overlapping changing content would contradict its purpose."""
        for anchor in m1_profile.stable_anchors:
            for dynamic in m1_profile.dynamic_ignore:
                overlaps = (
                    anchor.x < dynamic.right
                    and dynamic.x < anchor.right
                    and anchor.y < dynamic.bottom
                    and dynamic.y < anchor.bottom
                )
                assert not overlaps, f"anchor {anchor} overlaps dynamic {dynamic}"

    def test_dynamic_regions_include_both_scores(
        self, m1_profile: CalibrationProfile
    ) -> None:
        assert m1_profile.left_score in m1_profile.dynamic_ignore
        assert m1_profile.right_score in m1_profile.dynamic_ignore

    def test_scales_to_1080p(self, m1_profile: CalibrationProfile) -> None:
        scaled = m1_profile.for_frame_size(1920, 1080)
        scaled.validate()
        assert scaled.scoreboard == Region(536, 98, 864, 97)

    def test_is_bound_to_its_channel(self, m1_profile: CalibrationProfile) -> None:
        with pytest.raises(ChannelMismatchError):
            m1_profile.assert_channel("UCsomeOtherMaddenChannel")

    def test_stores_no_opponent_or_timing_information(self) -> None:
        """Guards against 49ers/Giants/scores/offsets leaking into the profile."""
        raw = M1_PROFILE_PATH.read_text().lower()
        for banned in ("49er", "giant", "niner", "sf", "nyg", "touchdown", "timestamp"):
            assert banned not in raw, f"fixture profile mentions {banned!r}"
