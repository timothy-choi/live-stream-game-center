"""Synthetic completed-game layouts for profile-driven finalization tests.

These images are test calibration fixtures. They are not a product-wide
postgame template and they are not bound to a particular commercial title.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import cv2
import numpy as np

from lsgc.calibration import CalibrationProfile, IdentityPresentation
from lsgc.calibration.geometry import Region
from lsgc.finalize.calibrate import calibrate_from_image
from lsgc.finalize.profile import FinalizationProfile
from lsgc.score.glyphs import GLYPH_HEIGHT, GLYPH_WIDTH, DigitGlyphSet, build_glyphs
from lsgc.score.reader import ScoreReader
from lsgc.vision.reference import ScoreboardReference


def digit_patch(digit: int) -> np.ndarray:
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


def glyph_set(profile: FinalizationProfile) -> DigitGlyphSet:
    """Templates extracted through the same preprocess path the reader uses."""
    board = CalibrationProfile(
        profile_id=profile.profile_id,
        channel_id=profile.channel_id,
        revision=1,
        reference_width=profile.reference_width,
        reference_height=profile.reference_height,
        scoreboard=profile.completion_panel,
        left_score=profile.left_final_score_region,
        right_score=profile.right_final_score_region,
        left_identity=Region(
            profile.completion_panel.x + 8,
            profile.completion_panel.y + 8,
            40,
            40,
        ),
        right_identity=Region(
            profile.completion_panel.right - 48,
            profile.completion_panel.y + 8,
            40,
            40,
        ),
        left_identity_presentation=IdentityPresentation.LOGO,
        right_identity_presentation=IdentityPresentation.LOGO,
        stable_anchors=profile.stable_anchors[:2],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    def sample(left: int, right: int) -> np.ndarray:
        image = np.full(
            (profile.reference_height, profile.reference_width, 3), 30, dtype=np.uint8
        )
        image[
            profile.completion_panel.y : profile.completion_panel.bottom,
            profile.completion_panel.x : profile.completion_panel.right,
        ] = (48, 42, 38)
        paint_score(image, profile.left_final_score_region, left)
        paint_score(image, profile.right_final_score_region, right)
        return image

    samples = [
        (sample(0, 1), 0, 1),
        (sample(2, 3), 2, 3),
        (sample(4, 5), 4, 5),
        (sample(6, 7), 6, 7),
        (sample(8, 9), 8, 9),
        (sample(14, 28), 14, 28),
    ]
    return build_glyphs(samples, board, notes="synthetic test glyphs")


def paint_score(image: np.ndarray, region: Region, value: int) -> None:
    image[region.y : region.bottom, region.x : region.right] = (20, 20, 20)
    digits = str(value)
    glyph_h = max(12, region.height - 8)
    glyph_w = max(8, int(round(glyph_h * GLYPH_WIDTH / GLYPH_HEIGHT)))
    gap = 6 if len(digits) > 1 else 0
    total = len(digits) * glyph_w + gap * (len(digits) - 1)
    x = region.x + max(1, (region.width - total) // 2)
    y = region.y + max(1, (region.height - glyph_h) // 2)
    for char in digits:
        patch = cv2.resize(
            digit_patch(int(char)), (glyph_w, glyph_h), interpolation=cv2.INTER_NEAREST
        )
        y1 = min(y + glyph_h, region.bottom)
        x1 = min(x + glyph_w, region.right)
        roi = image[y:y1, x:x1]
        mask = patch[: roi.shape[0], : roi.shape[1]]
        roi[mask > 40] = (255, 255, 255)
        x += glyph_w + gap


def _stripe_anchor(image: np.ndarray, region: Region, phase: int = 0) -> None:
    for step in range(0, region.height, 5):
        y = region.y + step
        x = region.x + ((step + phase) % max(1, region.width))
        cv2.line(image, (region.x, y), (min(x, region.right - 1), y), (240, 240, 240), 2)


def layout_a(
    *,
    channel_id: str = "UC_final_a",
    left: int = 21,
    right: int = 17,
    width: int = 1280,
    height: int = 720,
) -> tuple[np.ndarray, FinalizationProfile, ScoreboardReference, DigitGlyphSet]:
    """Central completed-game panel. Profile data for this fixture only."""
    image = np.full((height, width, 3), 18, dtype=np.uint8)
    image[0:height, 0:width] = (12, 28, 16)
    panel = Region(240, 80, 800, 540)
    image[panel.y : panel.bottom, panel.x : panel.right] = (48, 42, 38)
    cv2.rectangle(
        image,
        (panel.x, panel.y),
        (panel.right - 1, panel.bottom - 1),
        (210, 190, 160),
        6,
    )
    # Header chrome / tab-like blocks. Geometry only; no product text constants.
    cv2.rectangle(image, (260, 96), (420, 132), (90, 80, 70), -1)
    cv2.rectangle(image, (430, 96), (560, 132), (70, 62, 54), -1)
    cv2.rectangle(image, (860, 96), (1000, 132), (90, 80, 70), -1)
    # Stats-table grid (structure, not values).
    for row in range(0, 6):
        y = 280 + row * 40
        cv2.line(image, (280, y), (1000, y), (120, 110, 100), 1)
    cv2.line(image, (280, 260), (280, 500), (120, 110, 100), 1)
    cv2.line(image, (1000, 260), (1000, 500), (120, 110, 100), 1)
    # Lower control bar chrome.
    cv2.rectangle(image, (300, 560), (500, 600), (80, 90, 110), -1)
    cv2.rectangle(image, (780, 560), (980, 600), (80, 90, 110), -1)

    anchors = (
        Region(250, 90, 70, 48),
        Region(960, 90, 70, 48),
        Region(300, 555, 80, 50),
        Region(248, 300, 24, 80),
    )
    left_score = Region(360, 170, 72, 44)
    right_score = Region(850, 170, 72, 44)
    ignores = (
        Region(500, 90, 280, 50),  # result/winner text area
        Region(280, 150, 70, 70),  # left identity/artwork
        Region(930, 150, 70, 70),  # right identity/artwork
        left_score,
        right_score,
        Region(400, 280, 480, 220),  # statistics numbers
        Region(0, 0, 200, 720),  # background scene
        Region(1080, 0, 200, 720),
    )
    for anchor in anchors:
        _stripe_anchor(image, anchor, phase=anchor.x)
    paint_score(image, left_score, left)
    paint_score(image, right_score, right)

    profile, reference = calibrate_from_image(
        image,
        channel_id=channel_id,
        profile_id="layout-a",
        completion_panel=panel,
        stable_anchors=anchors,
        left_final_score_region=left_score,
        right_final_score_region=right_score,
        dynamic_ignore=ignores,
        notes="synthetic central-panel fixture; not a universal completed-game template",
    )
    return image, profile, reference, glyph_set(profile)


def layout_b(
    *,
    channel_id: str = "UC_final_b",
    left: int = 10,
    right: int = 3,
) -> tuple[np.ndarray, FinalizationProfile, ScoreboardReference, DigitGlyphSet]:
    """Corner card layout, deliberately unlike layout A."""
    image = np.full((720, 1280, 3), 40, dtype=np.uint8)
    panel = Region(860, 470, 380, 220)
    image[panel.y : panel.bottom, panel.x : panel.right] = (20, 55, 70)
    cv2.rectangle(
        image,
        (panel.x, panel.y),
        (panel.right - 1, panel.bottom - 1),
        (40, 220, 200),
        4,
    )
    cv2.rectangle(image, (870, 480), (1010, 510), (10, 90, 110), -1)
    cv2.rectangle(image, (1100, 640), (1220, 680), (10, 90, 110), -1)
    anchors = (
        Region(870, 478, 60, 36),
        Region(1160, 478, 60, 36),
        Region(1100, 638, 70, 40),
    )
    left_score = Region(900, 540, 64, 40)
    right_score = Region(1120, 540, 64, 40)
    for anchor in anchors:
        _stripe_anchor(image, anchor, phase=9)
    paint_score(image, left_score, left)
    paint_score(image, right_score, right)
    profile, reference = calibrate_from_image(
        image,
        channel_id=channel_id,
        profile_id="layout-b",
        completion_panel=panel,
        stable_anchors=anchors,
        left_final_score_region=left_score,
        right_final_score_region=right_score,
        notes="synthetic corner-card fixture for generality tests",
    )
    return image, profile, reference, glyph_set(profile)


def gameplay_imposter(profile: FinalizationProfile) -> np.ndarray:
    """Scoreboard-like band occupying similar coordinates with different structure."""
    image = np.full((profile.reference_height, profile.reference_width, 3), 30, dtype=np.uint8)
    image[40:110, 200:1080] = (70, 60, 50)
    for anchor in profile.stable_anchors:
        _stripe_anchor(image, anchor, phase=anchor.height)  # flipped cadence
    return image


def menu_frame(profile: FinalizationProfile) -> np.ndarray:
    image = np.full((profile.reference_height, profile.reference_width, 3), 8, dtype=np.uint8)
    cv2.rectangle(image, (400, 200), (880, 500), (90, 90, 90), -1)
    return image


def reader_for(profile: FinalizationProfile, glyphs: DigitGlyphSet) -> ScoreReader:
    return ScoreReader(
        glyphs,
        min_score=profile.reader_min_score,
        min_margin=profile.reader_min_margin,
        expect_channel=profile.channel_id,
    )


def scale_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)


def later(seconds: float) -> datetime:
    return datetime(2026, 9, 22, tzinfo=UTC) + timedelta(seconds=seconds)
