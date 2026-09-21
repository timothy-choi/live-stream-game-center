"""Score-crop extraction, digit reading, detector gating. Fully offline.

Real-recording validation lives in `tools/m6_validate.py`, which needs the
gitignored YouTube frames and is run by hand. These tests use synthetic
glyphs so pytest never needs Madden imagery or a network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region
from lsgc.models import Frame
from lsgc.score import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    GLYPH_HEIGHT,
    GLYPH_WIDTH,
    MAX_VALUE,
    DigitGlyphSet,
    GlyphMismatchError,
    RawScoreObservation,
    ScoreObservationPipeline,
    ScoreRead,
    ScoreReader,
    UnreadableReason,
    build_glyphs,
    extract_score_crops,
    load_glyphs,
)
from lsgc.vision import ScoreboardDetector, build_reference


def _profile() -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="score-test",
        channel_id="UC_score_channel",
        revision=1,
        reference_width=640,
        reference_height=360,
        scoreboard=Region(100, 40, 440, 60),
        left_score=Region(180, 55, 48, 28),
        right_score=Region(410, 55, 48, 28),
        left_identity=Region(110, 45, 60, 50),
        right_identity=Region(470, 45, 60, 50),
        left_identity_presentation=IdentityPresentation.LOGO,
        right_identity_presentation=IdentityPresentation.LOGO,
        stable_anchors=(Region(240, 48, 24, 44), Region(370, 48, 24, 44)),
        dynamic_ignore=(Region(300, 55, 40, 30),),
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def _digit_patch(digit: int) -> np.ndarray:
    """Distinct synthetic glyph, independent of the Madden score font."""
    patch = np.zeros((GLYPH_HEIGHT, GLYPH_WIDTH), dtype=np.uint8)
    cv2.putText(
        patch,
        str(digit),
        (1, GLYPH_HEIGHT - 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        255,
        1,
        cv2.LINE_AA,
    )
    return patch


def _glyph_set(profile: CalibrationProfile) -> DigitGlyphSet:
    """Templates extracted through the same preprocess path the reader uses."""
    samples = [
        (_present_frame(profile, 0, 1), 0, 1),
        (_present_frame(profile, 2, 3), 2, 3),
        (_present_frame(profile, 4, 5), 4, 5),
        (_present_frame(profile, 6, 7), 6, 7),
        (_present_frame(profile, 8, 9), 8, 9),
        (_present_frame(profile, 14, 28), 14, 28),
    ]
    return build_glyphs(samples, profile, notes="synthetic test glyphs")


def _paint_score(image: np.ndarray, region: Region, value: int, bg: tuple[int, int, int]) -> None:
    """Blit the same synthetic glyphs the reader will match against.

    Nearest-neighbour paste keeps the glyph identity through the reader's
    upscale-and-resize path, so these tests cover the pipeline rather than
    OpenCV's interpolation of Hershey fonts.
    """
    image[region.y : region.bottom, region.x : region.right] = bg
    digits = str(value)
    glyph_h = max(12, region.height - 8)
    glyph_w = max(8, int(round(glyph_h * GLYPH_WIDTH / GLYPH_HEIGHT)))
    gap = 6 if len(digits) > 1 else 0
    total = len(digits) * glyph_w + gap * (len(digits) - 1)
    x = region.x + max(1, (region.width - total) // 2)
    y = region.y + max(1, (region.height - glyph_h) // 2)
    for char in digits:
        patch = cv2.resize(
            _digit_patch(int(char)), (glyph_w, glyph_h), interpolation=cv2.INTER_NEAREST
        )
        y1 = min(y + glyph_h, region.bottom)
        x1 = min(x + glyph_w, region.right)
        roi = image[y:y1, x:x1]
        mask = patch[: roi.shape[0], : roi.shape[1]]
        roi[mask > 40] = (255, 255, 255)
        x += glyph_w + gap


def _draw_anchors(image: np.ndarray, profile: CalibrationProfile, flip: bool = False) -> None:
    for region in profile.stable_anchors:
        for step in range(0, region.height, 6):
            y = region.y + step
            offset = (region.height - step) if flip else step
            x = region.x + (offset % max(1, region.width))
            cv2.line(image, (region.x, y), (min(x, region.right - 1), y), (255, 255, 255), 2)


def _present_frame(
    profile: CalibrationProfile, left: int, right: int
) -> np.ndarray:
    image = np.full((profile.reference_height, profile.reference_width, 3), 30, dtype=np.uint8)
    image[
        profile.scoreboard.y : profile.scoreboard.bottom,
        profile.scoreboard.x : profile.scoreboard.right,
    ] = (70, 60, 50)
    _paint_score(image, profile.left_score, left, (40, 40, 180))
    _paint_score(image, profile.right_score, right, (180, 40, 40))
    _draw_anchors(image, profile)
    return image


@pytest.fixture
def profile() -> CalibrationProfile:
    return _profile()


@pytest.fixture
def glyphs(profile: CalibrationProfile) -> DigitGlyphSet:
    return _glyph_set(profile)


@pytest.fixture
def reader(glyphs: DigitGlyphSet) -> ScoreReader:
    return ScoreReader(glyphs)


class TestScoreCrops:
    def test_extracts_calibrated_regions(self, profile: CalibrationProfile) -> None:
        image = _present_frame(profile, 7, 14)
        left, right, fitted = extract_score_crops(image, profile)
        assert fitted.left_score == profile.left_score
        assert left.shape[0] == profile.left_score.height + 2  # 1px pad each side
        assert right.shape[1] == profile.right_score.width + 2
        assert left.size > 0 and right.size > 0

    def test_scales_with_the_profile_not_hard_coded_720p(
        self, profile: CalibrationProfile, reader: ScoreReader
    ) -> None:
        native = _present_frame(profile, 21, 0)
        scaled = cv2.resize(native, (1280, 720), interpolation=cv2.INTER_NEAREST)
        left_crop, right_crop, fitted = extract_score_crops(scaled, profile)
        assert (fitted.reference_width, fitted.reference_height) == (1280, 720)
        assert fitted.left_score != profile.left_score
        # Must not equal the Madden 720p fixture box.
        assert (fitted.left_score.x, fitted.left_score.y) != (470, 89)
        left, right = reader.read_image(scaled, profile)
        assert left == reader.read_crop(left_crop)
        assert left.readable and left.value == 21
        assert right.readable and right.value == 0


class TestReads:
    def test_single_digit(self, profile: CalibrationProfile, reader: ScoreReader) -> None:
        left, right = reader.read_image(_present_frame(profile, 7, 3), profile)
        assert left.readable and left.value == 7
        assert right.readable and right.value == 3

    def test_double_digit(self, profile: CalibrationProfile, reader: ScoreReader) -> None:
        left, right = reader.read_image(_present_frame(profile, 14, 28), profile)
        assert left.readable and left.value == 14
        assert right.readable and right.value == 28

    def test_zero(self, profile: CalibrationProfile, reader: ScoreReader) -> None:
        left, right = reader.read_image(_present_frame(profile, 0, 0), profile)
        assert left.readable and left.value == 0
        assert right.readable and right.value == 0

    def test_partial_left_readable_right_unreadable(
        self, profile: CalibrationProfile, reader: ScoreReader
    ) -> None:
        image = _present_frame(profile, 14, 7)
        # Destroy only the right crop.
        r = profile.right_score
        image[r.y : r.bottom, r.x : r.right] = (12, 40, 18)
        left, right = reader.read_image(image, profile)
        assert left.readable and left.value == 14
        assert not right.readable
        assert right.value is None
        assert right.reason is not None

    def test_empty_crop_is_unreadable(self, reader: ScoreReader) -> None:
        result = reader.read_crop(np.zeros((0, 0, 3), dtype=np.uint8))
        assert not result.readable
        assert result.value is None
        assert result.reason is UnreadableReason.EMPTY_CROP

    def test_malformed_noise_is_unreadable(self, reader: ScoreReader) -> None:
        rng = np.random.default_rng(0)
        crop = rng.integers(0, 40, (26, 59, 3), dtype=np.uint8)
        result = reader.read_crop(crop)
        assert not result.readable
        assert result.value is None

    def test_does_not_invent_a_score_on_a_blank_panel(self, reader: ScoreReader) -> None:
        crop = np.full((28, 50, 3), (40, 40, 160), dtype=np.uint8)
        result = reader.read_crop(crop)
        assert not result.readable
        assert result.value is None

    def test_confidence_is_a_quality_heuristic(
        self, profile: CalibrationProfile, reader: ScoreReader
    ) -> None:
        left, _ = reader.read_image(_present_frame(profile, 4, 2), profile)
        assert left.readable
        assert left.confidence is not None
        assert 0.0 <= left.confidence <= 1.0
        assert left.margin is not None
        assert left.confidence >= DEFAULT_MIN_SCORE
        assert left.margin >= DEFAULT_MIN_MARGIN

    def test_out_of_range_bound_is_documented(self) -> None:
        assert MAX_VALUE == 199


class TestGating:
    def test_absent_scoreboard_yields_no_observation(
        self, profile: CalibrationProfile, glyphs: DigitGlyphSet
    ) -> None:
        present = _present_frame(profile, 7, 14)
        reference = build_reference([present], profile)
        detector = ScoreboardDetector(profile, reference)
        pipeline = ScoreObservationPipeline(detector, ScoreReader(glyphs))

        imposter = np.full_like(present, 30)
        imposter[
            profile.scoreboard.y : profile.scoreboard.bottom,
            profile.scoreboard.x : profile.scoreboard.right,
        ] = (50, 60, 70)
        _draw_anchors(imposter, profile, flip=True)
        assert pipeline.observe(imposter) is None
        assert detector.detect(imposter).present is False

    def test_present_scoreboard_emits_raw_observation(
        self, profile: CalibrationProfile, glyphs: DigitGlyphSet
    ) -> None:
        present = _present_frame(profile, 21, 7)
        reference = build_reference([present], profile)
        detector = ScoreboardDetector(profile, reference)
        pipeline = ScoreObservationPipeline(detector, ScoreReader(glyphs))
        observation = pipeline.observe(present)
        assert isinstance(observation, RawScoreObservation)
        assert observation.detection_present
        assert observation.left.readable and observation.left.value == 21
        assert observation.right.readable and observation.right.value == 7
        assert observation.channel_id == profile.channel_id
        assert observation.profile_id == profile.profile_id
        assert observation.profile_revision == profile.revision
        assert observation.frame_width == 640
        assert observation.frame_height == 360

    def test_frame_metadata_is_copied_not_looked_up(
        self, profile: CalibrationProfile, glyphs: DigitGlyphSet
    ) -> None:
        present = _present_frame(profile, 0, 6)
        reference = build_reference([present], profile)
        pipeline = ScoreObservationPipeline(
            ScoreboardDetector(profile, reference), ScoreReader(glyphs)
        )
        received = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
        frame = Frame(
            index=17,
            image=present,
            width=640,
            height=360,
            timestamp_s=1234.5,
            source_url="https://example.invalid/watch?v=not-a-lookup-key",
            source_id="fakeid",
            received_at=received,
        )
        observation = pipeline.observe_frame(frame)
        assert observation is not None
        assert observation.frame_index == 17
        assert observation.timestamp_s == 1234.5
        assert observation.received_at == received
        assert observation.source_id == "fakeid"
        # Changing the timestamp without changing pixels cannot change the read.
        other = Frame(
            index=18, image=present, width=640, height=360,
            timestamp_s=0.0, source_url=frame.source_url, source_id="other",
        )
        again = pipeline.observe_frame(other)
        assert again is not None
        assert again.left.value == observation.left.value
        assert again.right.value == observation.right.value


class TestGlyphs:
    def test_build_from_labelled_frames_not_timestamps(
        self, profile: CalibrationProfile
    ) -> None:
        frames = [
            _present_frame(profile, 0, 1),
            _present_frame(profile, 2, 3),
            _present_frame(profile, 4, 5),
            _present_frame(profile, 6, 7),
            _present_frame(profile, 8, 9),
            _present_frame(profile, 14, 28),
        ]
        samples = [
            (frames[0], 0, 1),
            (frames[1], 2, 3),
            (frames[2], 4, 5),
            (frames[3], 6, 7),
            (frames[4], 8, 9),
            (frames[5], 14, 28),
        ]
        glyphs = build_glyphs(samples, profile, notes="synthetic construction")
        reader = ScoreReader(glyphs, min_score=0.5, min_margin=0.02)
        left, right = reader.read_image(_present_frame(profile, 21, 70), profile)
        assert left.readable and left.value == 21
        assert right.readable and right.value == 70

    def test_rejects_channel_mismatch(
        self, profile: CalibrationProfile, glyphs: DigitGlyphSet
    ) -> None:
        other = DigitGlyphSet(
            channel_id="UC_other",
            profile_id=glyphs.profile_id,
            patches=glyphs.patches,
            source_frame_count=1,
        )
        with pytest.raises(GlyphMismatchError):
            other.assert_matches(profile)

    def test_committed_glyphs_are_this_channel_only(self) -> None:
        path = Path(__file__).parent / "fixtures" / "m6_digit_glyphs.json"
        glyphs = load_glyphs(path, expect_channel="UCz3q0f-dMPNIyQhntPTE7gQ")
        assert set(glyphs.patches) == set(range(10))
        assert glyphs.patches[0].shape == (GLYPH_HEIGHT, GLYPH_WIDTH)


class TestNoLookup:
    def test_runtime_modules_have_no_timestamp_score_table(self) -> None:
        root = Path(__file__).resolve().parent.parent / "src" / "lsgc" / "score"
        forbidden = ("hBblAZD-ApY", "G2kMfIDFNIQ", "A8Qd1dKHKUE", "wDbkLLJT4i8", "UITXyHeI5KE")
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, f"{path.name} must not mention video id {token}"
            assert "offset_s" not in text
            assert "m6_score_validation_set" not in text


class TestScoreReadModel:
    def test_readable_rejects_a_none_value(self) -> None:
        with pytest.raises(ValueError):
            ScoreRead(
                value=None, readable=True, confidence=0.9, margin=0.2,
                raw_digits="7", reason=None,
            )

    def test_unreadable_rejects_a_value(self) -> None:
        with pytest.raises(ValueError):
            ScoreRead(
                value=7, readable=False, confidence=0.1, margin=0.0,
                raw_digits="", reason=UnreadableReason.NO_DIGITS,
            )
