"""Detector behaviour, channel safety and diagnostics. Fully offline.

Real-recording validation lives in `tools/m3_validate.py`, which needs YouTube
frames and is run by hand.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from conftest import M3_MANIFEST_PATH

from lsgc.calibration import (
    CalibrationProfile,
    CalibrationValidationError,
    ChannelMismatchError,
    Region,
)
from lsgc.vision import (
    DEFAULT_THRESHOLD,
    DetectionResult,
    ReferenceMismatchError,
    ScoreboardDetector,
    ScoreboardReference,
    VisionError,
    build_reference,
    detect_scoreboard,
    render_detection,
    save_detection,
)


@pytest.fixture
def detector(
    detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
) -> ScoreboardDetector:
    return ScoreboardDetector(detection_profile, scoreboard_reference)


class TestClearCases:
    def test_matching_evidence_is_present(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert result.present
        assert result.score > 0.99

    def test_mismatched_evidence_is_absent(
        self, detector: ScoreboardDetector, imposter_image: np.ndarray
    ) -> None:
        """Same frame size and same band occupied, different structure."""
        result = detector.detect(imposter_image)
        assert not result.present
        assert result.score < DEFAULT_THRESHOLD

    def test_blank_frame_is_absent(self, detector: ScoreboardDetector) -> None:
        result = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
        assert not result.present

    def test_flat_region_scores_zero_rather_than_dividing_by_zero(
        self, detector: ScoreboardDetector
    ) -> None:
        """No variation means no evidence, not an error and not a match."""
        result = detector.detect(np.full((360, 640, 3), 128, dtype=np.uint8))
        assert result.score == 0.0
        assert not result.present

    def test_brightness_and_contrast_shifts_still_match(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        """Correlation is normalised, so a dimmer feed must not flip the verdict."""
        dimmed = (scoreboard_image.astype(np.float32) * 0.55 + 20).astype(np.uint8)
        assert detector.detect(dimmed).present

    def test_noise_does_not_flip_the_verdict(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        rng = np.random.default_rng(1234)
        noise = rng.normal(0, 12, scoreboard_image.shape)
        noisy = np.clip(scoreboard_image + noise, 0, 255).astype(np.uint8)
        assert detector.detect(noisy).present


class TestStructuredResult:
    def test_reports_more_than_a_boolean(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert isinstance(result, DetectionResult)
        assert isinstance(result.present, bool)
        assert -1.0 <= result.score <= 1.0
        assert result.threshold == DEFAULT_THRESHOLD
        assert result.margin == pytest.approx(result.score - result.threshold)

    def test_carries_per_anchor_evidence(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert len(result.anchors) == 2
        for index, anchor in enumerate(result.anchors):
            assert anchor.index == index
            assert -1.0 <= anchor.intensity_similarity <= 1.0
            assert -1.0 <= anchor.structure_similarity <= 1.0
            assert anchor.score == min(
                anchor.intensity_similarity, anchor.structure_similarity
            )
            assert anchor.passed(result.threshold)

    def test_identifies_the_channel_and_calibration_used(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert result.channel_id == "UC_detect_channel"
        assert result.profile_id == "detect-test"
        assert result.profile_revision == 1

    def test_records_the_frame_size_inspected(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert (result.frame_width, result.frame_height) == (640, 360)

    def test_anchor_regions_are_in_frame_coordinates(
        self,
        detector: ScoreboardDetector,
        detection_profile: CalibrationProfile,
        scoreboard_image: np.ndarray,
    ) -> None:
        result = detector.detect(scoreboard_image)
        assert [a.region for a in result.anchors] == list(detection_profile.stable_anchors)

    def test_summary_explains_the_outcome(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        summary = detector.detect(scoreboard_image).summary()
        assert "PRESENT" in summary
        assert "anchor[0]" in summary and "anchor[1]" in summary

    def test_weakest_anchor_is_the_deciding_one(
        self, detector: ScoreboardDetector, imposter_image: np.ndarray
    ) -> None:
        result = detector.detect(imposter_image)
        assert result.weakest_anchor.score == result.score


class TestEveryAnchorMustAgree:
    def test_one_failing_anchor_rejects_the_frame(
        self, detector: ScoreboardDetector, detection_profile: CalibrationProfile,
        scoreboard_image: np.ndarray, imposter_image: np.ndarray
    ) -> None:
        """The conservative rule that keeps a partially-matching graphic out."""
        hybrid = scoreboard_image.copy()
        second = detection_profile.stable_anchors[1]
        hybrid[second.y : second.bottom, second.x : second.right] = imposter_image[
            second.y : second.bottom, second.x : second.right
        ]
        result = detector.detect(hybrid)
        assert result.anchors[0].score > DEFAULT_THRESHOLD
        assert not result.present
        assert result.weakest_anchor.index == 1


class TestThreshold:
    def test_boundary_is_inclusive(
        self, detection_profile: CalibrationProfile,
        scoreboard_reference: ScoreboardReference, scoreboard_image: np.ndarray
    ) -> None:
        score = ScoreboardDetector(detection_profile, scoreboard_reference).detect(
            scoreboard_image
        ).score
        at = ScoreboardDetector(detection_profile, scoreboard_reference, threshold=score)
        assert at.detect(scoreboard_image).present

    def test_just_above_the_score_rejects(
        self, detection_profile: CalibrationProfile,
        scoreboard_reference: ScoreboardReference, scoreboard_image: np.ndarray
    ) -> None:
        rng = np.random.default_rng(7)
        degraded = np.clip(
            scoreboard_image + rng.normal(0, 25, scoreboard_image.shape), 0, 255
        ).astype(np.uint8)
        score = ScoreboardDetector(detection_profile, scoreboard_reference).detect(
            degraded
        ).score
        assert score < 1.0, "need a sub-perfect score to probe the boundary"

        at = ScoreboardDetector(detection_profile, scoreboard_reference, threshold=score)
        above = ScoreboardDetector(
            detection_profile, scoreboard_reference, threshold=score + 1e-4
        )
        assert at.detect(degraded).present
        assert not above.detect(degraded).present

    def test_lowering_the_threshold_admits_a_mismatch(
        self, detection_profile: CalibrationProfile,
        scoreboard_reference: ScoreboardReference, imposter_image: np.ndarray
    ) -> None:
        permissive = ScoreboardDetector(
            detection_profile, scoreboard_reference, threshold=-1.0
        )
        assert permissive.detect(imposter_image).present

    @pytest.mark.parametrize("bad", [1.5, -2.0, 42.0])
    def test_out_of_range_threshold_rejected(
        self, detection_profile: CalibrationProfile,
        scoreboard_reference: ScoreboardReference, bad: float
    ) -> None:
        with pytest.raises(ValueError, match=r"threshold must lie in \[-1, 1\]"):
            ScoreboardDetector(detection_profile, scoreboard_reference, threshold=bad)

    def test_default_sits_inside_the_measured_gap(self) -> None:
        """Documents the M3 fixture measurement the default was chosen from."""
        assert -0.077 < DEFAULT_THRESHOLD < 0.831


class TestChannelAndReferenceSafety:
    def test_reference_from_another_channel_is_rejected_at_construction(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        other = replace(detection_profile, channel_id="UC_someone_else")
        with pytest.raises(ReferenceMismatchError, match="per-channel"):
            ScoreboardDetector(other, scoreboard_reference)

    def test_expect_channel_guards_the_profile(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        with pytest.raises(ChannelMismatchError):
            ScoreboardDetector(
                detection_profile, scoreboard_reference, expect_channel="UC_wrong"
            )

    def test_expect_channel_accepts_the_owning_channel(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        ScoreboardDetector(
            detection_profile, scoreboard_reference, expect_channel="UC_detect_channel"
        )

    def test_stale_reference_after_recalibration_is_rejected(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference
    ) -> None:
        recalibrated = detection_profile.next_revision(
            stable_anchors=(Region(250, 48, 24, 44), Region(380, 48, 24, 44))
        )
        with pytest.raises(ReferenceMismatchError):
            ScoreboardDetector(recalibrated, scoreboard_reference)

    def test_mismatch_cannot_be_bypassed_per_frame(
        self, detection_profile: CalibrationProfile, scoreboard_reference: ScoreboardReference,
        scoreboard_image: np.ndarray
    ) -> None:
        """Binding is checked once at construction, so no detect() call can skip it."""
        other = replace(detection_profile, channel_id="UC_someone_else")
        with pytest.raises(ReferenceMismatchError):
            detect_scoreboard(scoreboard_image, other, scoreboard_reference)


class TestResolution:
    def test_detects_on_a_proportionally_larger_frame(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        big = cv2.resize(scoreboard_image, (1280, 720), interpolation=cv2.INTER_LINEAR)
        result = detector.detect(big)
        assert result.present
        assert (result.frame_width, result.frame_height) == (1280, 720)

    def test_detects_on_a_proportionally_smaller_frame(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        small = cv2.resize(scoreboard_image, (320, 180), interpolation=cv2.INTER_AREA)
        assert detector.detect(small).present

    def test_anchor_regions_are_rescaled_for_the_frame(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        big = cv2.resize(scoreboard_image, (1280, 720), interpolation=cv2.INTER_LINEAR)
        result = detector.detect(big)
        assert result.anchors[0].region == Region(480, 96, 48, 88)

    def test_a_mismatch_stays_a_mismatch_after_rescaling(
        self, detector: ScoreboardDetector, imposter_image: np.ndarray
    ) -> None:
        big = cv2.resize(imposter_image, (1280, 720), interpolation=cv2.INTER_LINEAR)
        assert not detector.detect(big).present

    @pytest.mark.parametrize("size", [(640, 512), (720, 540), (1280, 1024)])
    def test_incompatible_aspect_ratio_fails_clearly(
        self, detector: ScoreboardDetector, size: tuple[int, int]
    ) -> None:
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        with pytest.raises(CalibrationValidationError, match="aspect ratio"):
            detector.detect(frame)

    def test_rescaled_geometry_is_cached_not_recomputed(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        big = cv2.resize(scoreboard_image, (1280, 720), interpolation=cv2.INTER_LINEAR)
        first = detector.detect(big)
        second = detector.detect(big)
        assert first.score == second.score
        assert set(detector._scaled) == {(1280, 720)}


class TestInputValidation:
    def test_rejects_a_non_bgr_image(self, detector: ScoreboardDetector) -> None:
        with pytest.raises(ReferenceMismatchError, match="HxWx3"):
            detector.detect(np.zeros((360, 640), dtype=np.uint8))

    def test_accepts_a_frame_from_the_ingestion_layer(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        from lsgc.models import Frame

        frame = Frame(
            index=0, image=scoreboard_image, width=640, height=360,
            timestamp_s=123.0, source_url="https://example.invalid/watch?v=x",
        )
        assert detector.detect_frame(frame).present

    def test_timestamp_does_not_influence_the_verdict(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        """Detection is a function of pixels and calibration only."""
        from lsgc.models import Frame

        def at(timestamp: float | None) -> float:
            return detector.detect_frame(
                Frame(index=0, image=scoreboard_image, width=640, height=360,
                      timestamp_s=timestamp, source_url="https://example.invalid/x")
            ).score

        assert at(0.0) == at(9999.0) == at(None)


class TestDeterminism:
    def test_repeated_detection_is_identical(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        scores = {detector.detect(scoreboard_image).score for _ in range(5)}
        assert len(scores) == 1

    def test_a_rebuilt_reference_gives_the_same_result(
        self, detection_profile: CalibrationProfile, scoreboard_image: np.ndarray
    ) -> None:
        a = build_reference([scoreboard_image], detection_profile)
        b = build_reference([scoreboard_image], detection_profile)
        assert np.array_equal(a.anchors[0].patch, b.anchors[0].patch)


class TestDiagnosticOverlay:
    def test_annotates_without_mutating_the_input(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        before = scoreboard_image.copy()
        annotated = render_detection(scoreboard_image, detector.detect(scoreboard_image))
        assert annotated.shape == scoreboard_image.shape
        assert np.array_equal(scoreboard_image, before)

    def test_marks_each_anchor(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        annotated = render_detection(scoreboard_image, result)
        for anchor in result.anchors:
            edge = annotated[anchor.region.y, anchor.region.x : anchor.region.right]
            original = scoreboard_image[anchor.region.y, anchor.region.x : anchor.region.right]
            assert not np.array_equal(edge, original)

    def test_pass_and_fail_are_visually_distinct(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray,
        imposter_image: np.ndarray
    ) -> None:
        good = render_detection(scoreboard_image, detector.detect(scoreboard_image))
        bad = render_detection(imposter_image, detector.detect(imposter_image))
        anchor = detector.detect(scoreboard_image).anchors[0].region
        assert not np.array_equal(
            good[anchor.y, anchor.x + 2], bad[anchor.y, anchor.x + 2]
        )

    def test_rejects_an_image_the_result_did_not_come_from(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray
    ) -> None:
        result = detector.detect(scoreboard_image)
        with pytest.raises(VisionError, match="Result describes"):
            render_detection(np.zeros((720, 1280, 3), dtype=np.uint8), result)

    def test_writes_a_readable_png(
        self, detector: ScoreboardDetector, scoreboard_image: np.ndarray, tmp_path: Path
    ) -> None:
        path = save_detection(
            scoreboard_image, detector.detect(scoreboard_image), tmp_path / "d" / "out.png"
        )
        assert cv2.imread(str(path)) is not None

    def test_default_output_stays_under_artifacts(self) -> None:
        from lsgc.vision.overlay import ARTIFACT_DIR

        assert ARTIFACT_DIR.parts[0] == "artifacts"


class TestValidationManifest:
    """The committed manifest is metadata; these guard its shape, not the model."""

    @pytest.fixture
    def manifest(self) -> dict:
        return json.loads(M3_MANIFEST_PATH.read_text())

    def test_has_positives_negatives_and_hard_negatives(self, manifest: dict) -> None:
        samples = manifest["samples"]
        assert len(samples) == 40
        assert sum(s["scoreboard_present"] for s in samples) == 33
        assert sum(not s["scoreboard_present"] for s in samples) == 7
        banners = [s for s in samples if "banner" in s["state"]]
        assert len(banners) == 4
        assert all(not s["scoreboard_present"] for s in banners)

    def test_every_sample_is_labelled(self, manifest: dict) -> None:
        for sample in manifest["samples"]:
            assert isinstance(sample["scoreboard_present"], bool)
            assert sample["state"].strip()
            assert isinstance(sample["offset_s"], (int, float))

    def test_offsets_are_unique(self, manifest: dict) -> None:
        offsets = [s["offset_s"] for s in manifest["samples"]]
        assert len(offsets) == len(set(offsets))

    def test_reference_frames_are_excluded_from_validation(self, manifest: dict) -> None:
        """Scoring the detector on the frames its reference came from would flatter it."""
        offsets = {s["offset_s"] for s in manifest["samples"]}
        assert not offsets & set(manifest["reference_frame_offsets"])

    def test_covers_the_required_negative_categories(self, manifest: dict) -> None:
        states = {s["state"] for s in manifest["samples"] if not s["scoreboard_present"]}
        assert "pregame_menu" in states
        assert "touchdown_banner" in states
        assert "extra_point_banner" in states

    def test_covers_varied_positive_conditions(self, manifest: dict) -> None:
        states = {s["state"] for s in manifest["samples"] if s["scoreboard_present"]}
        assert "normal_gameplay" in states
        assert "normal_gameplay_playcall_menu" in states
        assert "scoreboard_fading_in" in states

    def test_offsets_are_declared_fixture_metadata(self, manifest: dict) -> None:
        assert "never an input to detection" in manifest["offsets_are_fixture_metadata"]
