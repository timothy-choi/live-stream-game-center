"""Identity crop routing, logo rejection, and M3 gating. Synthetic imagery."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region
from lsgc.identity.logos import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    LogoRecognizer,
    LogoTemplate,
    LogoTemplateSet,
    build_logo_templates,
    descriptor_from_crop,
    extract_identity_crops,
    mean_bgr,
)
from lsgc.identity.models import IdentityMethod, TeamIdentityObservation, UnreadableIdentityReason
from lsgc.identity.pipeline import MatchupObservationPipeline
from lsgc.identity.recognizer import IdentityRecognizer
from lsgc.identity.text import UnvalidatedTextReader
from lsgc.models import Frame
from lsgc.vision.detector import DetectionResult


def _profile(*, presentation: IdentityPresentation = IdentityPresentation.LOGO) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="id-synth",
        channel_id="UC_identity",
        revision=1,
        reference_width=320,
        reference_height=180,
        scoreboard=Region(40, 20, 240, 40),
        left_score=Region(70, 28, 30, 20),
        right_score=Region(220, 28, 30, 20),
        left_identity=Region(45, 22, 24, 18),
        right_identity=Region(250, 22, 24, 18),
        left_identity_presentation=presentation,
        right_identity_presentation=presentation,
        stable_anchors=(Region(100, 22, 12, 16),),
        dynamic_ignore=(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _paint(image: np.ndarray, region: Region, color: tuple[int, int, int], mark: int) -> None:
    image[region.y : region.y + region.height, region.x : region.x + region.width] = color
    cv2.line(
        image,
        (region.x + mark, region.y),
        (region.x + mark, region.y + region.height - 1),
        (255, 255, 255),
        1,
    )


def _frame_for(profile: CalibrationProfile, left_color, right_color, left_mark, right_mark) -> np.ndarray:
    image = np.full((profile.reference_height, profile.reference_width, 3), 20, dtype=np.uint8)
    _paint(image, profile.left_identity, left_color, left_mark)
    _paint(image, profile.right_identity, right_color, right_mark)
    return image


TEAMS = {
    "SF": ((0, 0, 200), 4),
    "GB": ((0, 160, 20), 8),
    "MIN": ((120, 20, 80), 12),
    "NYG": ((180, 40, 20), 16),
}


def _templates(profile: CalibrationProfile) -> LogoTemplateSet:
    samples = []
    for team_id, (color, mark) in TEAMS.items():
        image = _frame_for(profile, color, color, mark, mark)
        left, _right = extract_identity_crops(image, profile)
        samples.append((left, team_id))
    return build_logo_templates(profile, samples)


class TestLogoRecognizer:
    def test_reads_painted_team_and_rejects_blank(self) -> None:
        profile = _profile()
        templates = _templates(profile)
        recognizer = LogoRecognizer(templates)
        image = _frame_for(profile, TEAMS["SF"][0], TEAMS["GB"][0], TEAMS["SF"][1], TEAMS["GB"][1])
        left_crop, right_crop = extract_identity_crops(image, profile)
        left = recognizer.read_crop(left_crop)
        right = recognizer.read_crop(right_crop)
        assert left.readable and left.team_id == "SF"
        assert right.readable and right.team_id == "GB"
        assert left.confidence is not None and left.confidence >= DEFAULT_MIN_SCORE
        assert left.margin is not None and left.margin >= DEFAULT_MIN_MARGIN
        blank = recognizer.read_crop(np.zeros((18, 24, 3), dtype=np.uint8))
        assert not blank.readable
        assert blank.reason in {
            UnreadableIdentityReason.LOW_CONFIDENCE,
            UnreadableIdentityReason.AMBIGUOUS,
            UnreadableIdentityReason.EMPTY_CROP,
        }

    def test_does_not_always_return_nearest(self) -> None:
        profile = _profile()
        templates = _templates(profile)
        recognizer = LogoRecognizer(templates, min_score=0.99, min_margin=0.5)
        mixed = np.full((18, 24, 3), 80, dtype=np.uint8)
        obs = recognizer.read_crop(mixed)
        assert not obs.readable
        assert obs.team_id is None


class TestPresentationRouting:
    def test_logo_presentation_uses_logo(self) -> None:
        profile = _profile(presentation=IdentityPresentation.LOGO)
        templates = _templates(profile)
        ident = IdentityRecognizer(profile, logos=LogoRecognizer(templates))
        image = _frame_for(profile, TEAMS["SF"][0], TEAMS["NYG"][0], TEAMS["SF"][1], TEAMS["NYG"][1])
        left, right = ident.recognize_image(image)
        assert left.method is IdentityMethod.LOGO and left.team_id == "SF"
        assert right.team_id == "NYG"

    def test_text_first_when_name_presentation_is_readable(self) -> None:
        profile = _profile(presentation=IdentityPresentation.NAME)

        def reader(crop, presentation):
            return TeamIdentityObservation.known(
                "LAC",
                method=IdentityMethod.TEXT_NAME,
                confidence=1.0,
                margin=1.0,
            )

        ident = IdentityRecognizer(
            profile, logos=LogoRecognizer(_templates(profile)), text_reader=reader
        )
        image = _frame_for(profile, TEAMS["SF"][0], TEAMS["GB"][0], TEAMS["SF"][1], TEAMS["GB"][1])
        left, _right = ident.recognize_image(image)
        assert left.team_id == "LAC"
        assert left.method is IdentityMethod.TEXT_NAME

    def test_unvalidated_text_falls_back_to_logo(self) -> None:
        profile = _profile(presentation=IdentityPresentation.NAME | IdentityPresentation.LOGO)
        ident = IdentityRecognizer(
            profile,
            logos=LogoRecognizer(_templates(profile)),
            text_reader=UnvalidatedTextReader().read_crop,
        )
        image = _frame_for(profile, TEAMS["MIN"][0], TEAMS["GB"][0], TEAMS["MIN"][1], TEAMS["GB"][1])
        left, right = ident.recognize_image(image)
        assert left.team_id == "MIN" and left.method is IdentityMethod.LOGO
        assert right.team_id == "GB"

    def test_scaled_crop_geometry(self) -> None:
        profile = _profile()
        big = np.zeros((360, 640, 3), dtype=np.uint8)
        fitted = profile.for_frame_size(640, 360)
        assert fitted.left_identity.width == profile.left_identity.width * 2
        left, right = extract_identity_crops(big, profile)
        assert left.shape[1] == fitted.left_identity.width
        assert right.shape[0] == fitted.right_identity.height


class TestScoreboardGating:
    def test_absent_detection_returns_none(self) -> None:
        profile = _profile()
        ident = IdentityRecognizer(profile, logos=LogoRecognizer(_templates(profile)))
        detector = SimpleNamespace(profile=profile)
        pipeline = MatchupObservationPipeline(detector, ident)  # type: ignore[arg-type]
        image = _frame_for(profile, TEAMS["SF"][0], TEAMS["GB"][0], TEAMS["SF"][1], TEAMS["GB"][1])
        frame = Frame(
            index=0,
            image=image,
            width=image.shape[1],
            height=image.shape[0],
            timestamp_s=1.0,
            source_url="file://x",
            received_at=datetime.now(tz=UTC),
        )
        detection = DetectionResult(
            present=False,
            score=-0.2,
            threshold=0.55,
            anchors=(),
            frame_width=image.shape[1],
            frame_height=image.shape[0],
            channel_id=profile.channel_id,
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
        )
        assert pipeline.observe_with_detection(frame, detection) is None
