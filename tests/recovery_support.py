"""Gameplay scoreboard frames for missed-scoring recovery tests.

Synthetic glyphs only. No sport-specific scoring vocabulary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import cv2
import numpy as np

from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region
from lsgc.models import Frame
from lsgc.score import DigitGlyphSet, ScoreObservationPipeline, ScoreReader, build_glyphs
from lsgc.score.glyphs import GLYPH_HEIGHT, GLYPH_WIDTH
from lsgc.vision import ScoreboardDetector, build_reference


def gameplay_profile() -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="recovery-score-test",
        channel_id="UC_recovery_channel",
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


def _paint_score(image: np.ndarray, region: Region, value: int, bg: tuple[int, int, int]) -> None:
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


def present_scoreboard(profile: CalibrationProfile, left: int, right: int) -> np.ndarray:
    image = np.full((profile.reference_height, profile.reference_width, 3), 30, dtype=np.uint8)
    image[
        profile.scoreboard.y : profile.scoreboard.bottom,
        profile.scoreboard.x : profile.scoreboard.right,
    ] = (70, 60, 50)
    _paint_score(image, profile.left_score, left, (40, 40, 180))
    _paint_score(image, profile.right_score, right, (180, 40, 40))
    for region in profile.stable_anchors:
        for step in range(0, region.height, 6):
            y = region.y + step
            x = region.x + (step % max(1, region.width))
            cv2.line(image, (region.x, y), (min(x, region.right - 1), y), (255, 255, 255), 2)
    return image


def gameplay_glyphs(profile: CalibrationProfile) -> DigitGlyphSet:
    samples = [
        (present_scoreboard(profile, 0, 1), 0, 1),
        (present_scoreboard(profile, 2, 3), 2, 3),
        (present_scoreboard(profile, 4, 5), 4, 5),
        (present_scoreboard(profile, 6, 7), 6, 7),
        (present_scoreboard(profile, 8, 9), 8, 9),
        (present_scoreboard(profile, 14, 28), 14, 28),
        (present_scoreboard(profile, 21, 27), 21, 27),
        (present_scoreboard(profile, 28, 31), 28, 31),
        (present_scoreboard(profile, 33, 42), 33, 42),
        (present_scoreboard(profile, 35, 41), 35, 41),
        (present_scoreboard(profile, 35, 42), 35, 42),
        (present_scoreboard(profile, 35, 48), 35, 48),
        (present_scoreboard(profile, 35, 49), 35, 49),
    ]
    return build_glyphs(samples, profile, notes="recovery synthetic glyphs")


def gameplay_pipeline() -> tuple[CalibrationProfile, ScoreObservationPipeline]:
    profile = gameplay_profile()
    glyphs = gameplay_glyphs(profile)
    reference = build_reference([present_scoreboard(profile, 0, 0)], profile)
    detector = ScoreboardDetector(profile, reference)
    pipeline = ScoreObservationPipeline(detector, ScoreReader(glyphs))
    return profile, pipeline


def score_frame(
    profile: CalibrationProfile,
    left: int,
    right: int,
    *,
    index: int,
    timestamp_s: float,
) -> Frame:
    image = present_scoreboard(profile, left, right)
    height, width = image.shape[:2]
    return Frame(
        index=index,
        image=image,
        width=width,
        height=height,
        timestamp_s=timestamp_s,
        source_url="recovery://fixture",
        source_id="fixture",
        received_at=datetime.now(tz=UTC),
        extra={"source_epoch": 1},
    )


def held_score_frames(
    profile: CalibrationProfile,
    pairs: list[tuple[int, int]],
    *,
    start_s: float,
    hold: int = 2,
    interval_s: float = 0.5,
    index0: int = 0,
    until_s: float | None = None,
) -> list[Frame]:
    frames: list[Frame] = []
    timestamp = start_s
    index = index0
    for left, right in pairs:
        for _ in range(hold):
            frames.append(
                score_frame(profile, left, right, index=index, timestamp_s=timestamp)
            )
            timestamp += interval_s
            index += 1
    if until_s is not None and pairs and frames:
        left, right = pairs[-1]
        while frames[-1].timestamp_s is not None and frames[-1].timestamp_s < until_s:
            frames.append(
                score_frame(profile, left, right, index=index, timestamp_s=timestamp)
            )
            timestamp += interval_s
            index += 1
    return frames
