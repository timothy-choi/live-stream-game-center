"""Reference evidence: building, binding and serialization. Fully offline."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import M3_REFERENCE_PATH

from lsgc.calibration import CalibrationProfile, Region
from lsgc.vision import (
    DESCRIPTOR_HEIGHT,
    DESCRIPTOR_WIDTH,
    REFERENCE_SCHEMA_VERSION,
    ReferenceMismatchError,
    ReferenceSchemaError,
    ScoreboardReference,
    build_reference,
    load_reference,
    load_reference_unchecked,
    reference_from_dict,
    reference_to_dict,
    save_reference,
)


class TestBuildReference:
    def test_one_patch_per_anchor_on_the_descriptor_grid(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        reference = build_reference([scoreboard_image], detection_profile)
        assert len(reference.anchors) == len(detection_profile.stable_anchors)
        for anchor in reference.anchors:
            assert anchor.patch.shape == (DESCRIPTOR_HEIGHT, DESCRIPTOR_WIDTH)
            assert anchor.patch.dtype == np.uint8

    def test_binds_to_the_profile_it_was_built_from(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        reference = build_reference([scoreboard_image], detection_profile)
        assert reference.channel_id == detection_profile.channel_id
        assert reference.profile_id == detection_profile.profile_id
        assert reference.profile_revision == detection_profile.revision

    def test_records_each_anchor_region(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        reference = build_reference([scoreboard_image], detection_profile)
        regions = [anchor.region for anchor in reference.anchors]
        assert regions == list(detection_profile.stable_anchors)

    def test_averages_multiple_frames(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        brighter = np.clip(scoreboard_image.astype(np.int16) + 40, 0, 255).astype(np.uint8)
        single = build_reference([scoreboard_image], detection_profile)
        averaged = build_reference([scoreboard_image, brighter], detection_profile)
        assert averaged.source_frame_count == 2
        assert averaged.anchors[0].patch.mean() > single.anchors[0].patch.mean()

    def test_captures_real_structure(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        """A reference of a flat patch would make every comparison meaningless."""
        reference = build_reference([scoreboard_image], detection_profile)
        assert all(anchor.patch.std() > 5 for anchor in reference.anchors)

    def test_builds_from_a_rescaled_frame(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        import cv2

        big = cv2.resize(scoreboard_image, (1280, 720), interpolation=cv2.INTER_NEAREST)
        reference = build_reference([big], detection_profile)
        # Regions are always recorded in the profile's reference space.
        assert reference.anchors[0].region == detection_profile.stable_anchors[0]

    def test_requires_at_least_one_frame(
        self, detection_profile: CalibrationProfile
    ) -> None:
        with pytest.raises(ReferenceMismatchError, match="(?i)at least one"):
            build_reference([], detection_profile)

    def test_rejects_non_bgr_images(self, detection_profile: CalibrationProfile) -> None:
        with pytest.raises(ReferenceMismatchError, match="HxWx3"):
            build_reference([np.zeros((360, 640), dtype=np.uint8)], detection_profile)


class TestBindingToProfile:
    def test_matching_profile_passes(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        scoreboard_reference.assert_matches(detection_profile)

    def test_rejects_another_channel(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        other = replace(detection_profile, channel_id="UC_someone_else")
        with pytest.raises(ReferenceMismatchError, match="per-channel"):
            scoreboard_reference.assert_matches(other)

    def test_rejects_another_profile_lineage(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        with pytest.raises(ReferenceMismatchError, match="built for profile"):
            scoreboard_reference.assert_matches(replace(detection_profile, profile_id="other"))

    def test_rejects_a_different_revision(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        """Recalibrating invalidates the patches sampled against the old geometry."""
        with pytest.raises(ReferenceMismatchError, match="Rebuild the reference"):
            scoreboard_reference.assert_matches(detection_profile.next_revision())

    def test_rejects_a_changed_anchor_count(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        fewer = replace(
            detection_profile,
            revision=1,
            stable_anchors=(detection_profile.stable_anchors[0],),
        )
        with pytest.raises(ReferenceMismatchError, match="describes 2 anchors"):
            scoreboard_reference.assert_matches(fewer)

    def test_rejects_a_moved_anchor(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        moved = replace(
            detection_profile,
            stable_anchors=(Region(250, 48, 24, 44), detection_profile.stable_anchors[1]),
        )
        with pytest.raises(ReferenceMismatchError, match="moved since the reference"):
            scoreboard_reference.assert_matches(moved)


class TestSerialization:
    def test_dict_round_trip(self, scoreboard_reference: ScoreboardReference) -> None:
        restored = reference_from_dict(reference_to_dict(scoreboard_reference))
        assert restored.channel_id == scoreboard_reference.channel_id
        assert restored.profile_revision == scoreboard_reference.profile_revision
        for before, after in zip(scoreboard_reference.anchors, restored.anchors):
            assert np.array_equal(before.patch, after.patch)
            assert before.region == after.region

    def test_file_round_trip(
        self, scoreboard_reference: ScoreboardReference, tmp_path: Path
    ) -> None:
        path = save_reference(scoreboard_reference, tmp_path / "ref.json")
        restored = load_reference_unchecked(path)
        assert np.array_equal(
            restored.anchors[0].patch, scoreboard_reference.anchors[0].patch
        )

    def test_output_is_byte_stable(
        self, scoreboard_reference: ScoreboardReference, tmp_path: Path
    ) -> None:
        first = save_reference(scoreboard_reference, tmp_path / "a.json").read_bytes()
        second = save_reference(scoreboard_reference, tmp_path / "b.json").read_bytes()
        assert first == second

    def test_schema_version_is_the_first_key(
        self, scoreboard_reference: ScoreboardReference
    ) -> None:
        assert next(iter(reference_to_dict(scoreboard_reference))) == "schema_version"

    def test_patches_serialize_as_plain_integers(
        self, scoreboard_reference: ScoreboardReference, tmp_path: Path
    ) -> None:
        """Reference evidence stays reviewable JSON, not an opaque blob."""
        path = save_reference(scoreboard_reference, tmp_path / "ref.json")
        data = json.loads(path.read_text())
        patch = data["anchors"][0]["patch"]
        assert len(patch) == DESCRIPTOR_HEIGHT
        assert len(patch[0]) == DESCRIPTOR_WIDTH
        assert all(isinstance(value, int) and 0 <= value <= 255 for value in patch[0])


class TestMalformedReference:
    def test_unsupported_schema_version(
        self, scoreboard_reference: ScoreboardReference
    ) -> None:
        data = reference_to_dict(scoreboard_reference)
        data["schema_version"] = REFERENCE_SCHEMA_VERSION + 1
        with pytest.raises(ReferenceSchemaError, match="Unsupported reference schema_version"):
            reference_from_dict(data)

    def test_missing_schema_version(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        del data["schema_version"]
        with pytest.raises(ReferenceSchemaError, match="missing schema_version"):
            reference_from_dict(data)

    def test_missing_required_fields(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        del data["channel_id"]
        with pytest.raises(ReferenceSchemaError, match="missing required fields"):
            reference_from_dict(data)

    def test_descriptor_grid_mismatch_is_explicit(
        self, scoreboard_reference: ScoreboardReference
    ) -> None:
        """A reference built by a build using another grid cannot be compared."""
        data = reference_to_dict(scoreboard_reference)
        data["descriptor_width"] = DESCRIPTOR_WIDTH + 4
        with pytest.raises(ReferenceSchemaError, match="descriptor grid"):
            reference_from_dict(data)

    def test_patch_of_the_wrong_shape(
        self, scoreboard_reference: ScoreboardReference
    ) -> None:
        data = reference_to_dict(scoreboard_reference)
        data["anchors"][0]["patch"] = [[1, 2, 3], [4, 5, 6]]
        with pytest.raises(ReferenceSchemaError, match="patch must be"):
            reference_from_dict(data)

    def test_non_numeric_patch(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        data["anchors"][0]["patch"] = [["a"] * DESCRIPTOR_WIDTH] * DESCRIPTOR_HEIGHT
        with pytest.raises(ReferenceSchemaError, match="not numeric"):
            reference_from_dict(data)

    def test_anchor_without_a_region(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        del data["anchors"][0]["region"]
        with pytest.raises(ReferenceSchemaError, match="needs a region"):
            reference_from_dict(data)

    def test_no_anchors(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        data["anchors"] = []
        with pytest.raises(ReferenceSchemaError, match="at least one anchor"):
            reference_from_dict(data)

    def test_not_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{oops")
        with pytest.raises(ReferenceSchemaError, match="not valid JSON"):
            load_reference_unchecked(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ReferenceSchemaError, match="Could not read reference"):
            load_reference_unchecked(tmp_path / "absent.json")

    def test_bad_timestamp(self, scoreboard_reference: ScoreboardReference) -> None:
        data = reference_to_dict(scoreboard_reference)
        data["created_at"] = "whenever"
        with pytest.raises(ReferenceSchemaError, match="created_at"):
            reference_from_dict(data)


class TestLoadTimeChannelBinding:
    def test_load_requires_the_expected_channel(
        self, scoreboard_reference: ScoreboardReference, tmp_path: Path
    ) -> None:
        path = save_reference(scoreboard_reference, tmp_path / "ref.json")
        assert load_reference(path, expect_channel="UC_detect_channel").channel_id == (
            "UC_detect_channel"
        )

    def test_load_rejects_another_channel(
        self, scoreboard_reference: ScoreboardReference, tmp_path: Path
    ) -> None:
        path = save_reference(scoreboard_reference, tmp_path / "ref.json")
        with pytest.raises(ReferenceMismatchError, match="not 'UC_other'"):
            load_reference(path, expect_channel="UC_other")

    def test_expected_channel_is_not_optional(self) -> None:
        import inspect

        parameter = inspect.signature(load_reference).parameters["expect_channel"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


class TestCommittedMaddenReference:
    """The reference committed alongside the M1 fixture profile.

    Metadata and small structural descriptors only; the frames it was sampled
    from are not in git.
    """

    CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"

    def test_loads_and_binds_to_the_fixture_profile(self) -> None:
        from conftest import M1_PROFILE_PATH

        from lsgc.calibration import load_profile

        profile = load_profile(M1_PROFILE_PATH, expect_channel=self.CHANNEL)
        reference = load_reference(M3_REFERENCE_PATH, expect_channel=self.CHANNEL)
        reference.assert_matches(profile)
        assert len(reference.anchors) == 2

    def test_is_bound_to_its_channel(self) -> None:
        with pytest.raises(ReferenceMismatchError):
            load_reference(M3_REFERENCE_PATH, expect_channel="UCsomeOtherChannel")

    def test_patches_carry_structure(self) -> None:
        reference = load_reference_unchecked(M3_REFERENCE_PATH)
        assert all(anchor.patch.std() > 20 for anchor in reference.anchors)

    def test_stores_no_opponent_score_or_timing_information(self) -> None:
        raw = M3_REFERENCE_PATH.read_text().lower()
        for banned in ("49er", "giant", "niner", "nyg", "touchdown", "offset", "timestamp"):
            assert banned not in raw, f"reference mentions {banned!r}"

    def test_is_small_enough_to_review(self) -> None:
        values = DESCRIPTOR_WIDTH * DESCRIPTOR_HEIGHT * 2
        assert values == 576
        assert M3_REFERENCE_PATH.stat().st_size < 40_000
