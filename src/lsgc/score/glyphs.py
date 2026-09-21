"""Digit glyph set: 0–9 grayscale patches for this channel's score font.

Glyphs are built from a small, named construction set of frames and stored as
reviewable JSON (same idea as M3's scoreboard reference: a few thousand
integers, no binary crops). They describe digit *shape*, not teams, not
scores-at-timestamps, and they are never a lookup table from video offset to
value.

Construction frames are excluded from held-out validation metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from lsgc.calibration.profile import CalibrationProfile
from lsgc.score.errors import GlyphMismatchError, GlyphSchemaError
from lsgc.score.preprocess import (
    digit_boxes,
    extract_region,
    glyph_patch,
    localisation_mask,
    min_channel,
    upscale_gray,
)

GLYPH_SCHEMA_VERSION = 1
GLYPH_WIDTH = 18
GLYPH_HEIGHT = 28
"""Descriptor grid per digit. Small enough for JSON, large enough that 0 vs 8
and 3 vs 5 keep distinct holes after averaging a few construction examples."""


def zero_normalised_correlation(probe: np.ndarray, reference: np.ndarray) -> float:
    """Correlation in [-1, 1], insensitive to brightness offset and scale."""
    a = probe.astype(np.float32)
    b = reference.astype(np.float32)
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denominator < 1e-6:
        return 0.0
    return float(np.clip((a * b).sum() / denominator, -1.0, 1.0))


@dataclass(frozen=True)
class DigitGlyphSet:
    """Grayscale templates for digits 0–9 of one channel's score font."""

    channel_id: str
    profile_id: str
    patches: dict[int, np.ndarray]
    """digit -> GLYPH_HEIGHT x GLYPH_WIDTH uint8."""

    source_frame_count: int
    created_at: datetime = None  # type: ignore[assignment]
    notes: str = ""
    schema_version: int = GLYPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(UTC))
        if self.schema_version != GLYPH_SCHEMA_VERSION:
            raise GlyphSchemaError(
                f"Unsupported glyph schema_version {self.schema_version}; "
                f"this build reads version {GLYPH_SCHEMA_VERSION}."
            )
        expected = set(range(10))
        got = set(self.patches)
        if got != expected:
            raise GlyphSchemaError(
                f"Glyph set must contain digits 0-9, missing {sorted(expected - got)}, "
                f"extra {sorted(got - expected)}."
            )
        frozen: dict[int, np.ndarray] = {}
        for digit, patch in self.patches.items():
            if patch.shape != (GLYPH_HEIGHT, GLYPH_WIDTH):
                raise GlyphSchemaError(
                    f"Digit {digit} patch must be {GLYPH_HEIGHT}x{GLYPH_WIDTH}, "
                    f"got {patch.shape}"
                )
            if patch.dtype != np.uint8:
                raise GlyphSchemaError(
                    f"Digit {digit} patch must be uint8, got {patch.dtype}"
                )
            frozen[digit] = np.ascontiguousarray(patch)
        object.__setattr__(self, "patches", frozen)

    def assert_channel(self, channel_id: str) -> None:
        if self.channel_id != channel_id:
            raise GlyphMismatchError(
                f"Glyphs belong to channel {self.channel_id!r}, not {channel_id!r}."
            )

    def assert_matches(self, profile: CalibrationProfile) -> None:
        self.assert_channel(profile.channel_id)
        if self.profile_id != profile.profile_id:
            raise GlyphMismatchError(
                f"Glyphs were built for profile {self.profile_id!r}, "
                f"not {profile.profile_id!r}."
            )

    def match(self, patch: np.ndarray) -> tuple[int, float, float]:
        """Best digit, its ZNCC, and the gap down to the runner-up."""
        scores = {
            digit: zero_normalised_correlation(patch, template)
            for digit, template in self.patches.items()
        }
        ranked = sorted(scores.items(), key=lambda item: -item[1])
        best_digit, best_score = ranked[0]
        second = ranked[1][1]
        return best_digit, best_score, best_score - second

    def summary(self) -> str:
        lines = [
            f"channel_id   {self.channel_id}",
            f"profile      {self.profile_id}",
            f"glyph size   {GLYPH_WIDTH}x{GLYPH_HEIGHT} grayscale",
            f"built from   {self.source_frame_count} construction frame(s)",
            f"created_at   {self.created_at.isoformat()}",
        ]
        lines += [
            f"  digit {digit}  mean={self.patches[digit].mean():.1f} "
            f"std={self.patches[digit].std():.1f}"
            for digit in range(10)
        ]
        if self.notes:
            lines.append(f"notes        {self.notes}")
        return "\n".join(lines)


def extract_digit_patches(
    crop: np.ndarray, expected_digits: str
) -> list[tuple[int, np.ndarray]]:
    """Localise digits in a labelled construction crop.

    `expected_digits` is the visible value as decimal digits, e.g. `"0"` or
    `"14"`. The crop must split into exactly that many boxes or this raises.
    """
    if crop.size == 0:
        raise GlyphSchemaError("Construction crop is empty.")
    upscaled = upscale_gray(min_channel(crop))
    boxes = digit_boxes(localisation_mask(upscaled))
    if len(boxes) != len(expected_digits):
        raise GlyphSchemaError(
            f"Construction crop split into {len(boxes)} digit(s), "
            f"expected {len(expected_digits)} from {expected_digits!r}."
        )
    out: list[tuple[int, np.ndarray]] = []
    for box, char in zip(boxes, expected_digits):
        patch = glyph_patch(upscaled, box, width=GLYPH_WIDTH, height=GLYPH_HEIGHT)
        if patch is None:
            raise GlyphSchemaError(f"Empty glyph patch for digit {char!r}.")
        out.append((int(char), patch))
    return out


def build_glyphs(
    samples: list[tuple[np.ndarray, int, int]],
    profile: CalibrationProfile,
    *,
    notes: str = "",
    created_at: datetime | None = None,
) -> DigitGlyphSet:
    """Average per-digit patches from labelled construction crops.

    Each sample is `(bgr_frame, left_score, right_score)`. Frames must show
    the digits being templated; this does not consult any timestamp table.
    """
    if not samples:
        raise GlyphSchemaError("At least one construction frame is required.")

    stacks: dict[int, list[np.ndarray]] = {digit: [] for digit in range(10)}
    for image, left_value, right_value in samples:
        height, width = image.shape[:2]
        fitted = profile.for_frame_size(width, height)
        for region, value in (
            (fitted.left_score, left_value),
            (fitted.right_score, right_value),
        ):
            if value < 0:
                raise GlyphSchemaError(f"Construction score must be non-negative, got {value}")
            crop = extract_region(image, region)
            for digit, patch in extract_digit_patches(crop, str(value)):
                stacks[digit].append(patch.astype(np.float32))

    missing = [digit for digit, stack in stacks.items() if not stack]
    if missing:
        raise GlyphSchemaError(
            f"Construction set did not yield glyphs for digits {missing}."
        )

    patches = {
        digit: np.round(np.mean(stack, axis=0)).astype(np.uint8)
        for digit, stack in stacks.items()
    }
    return DigitGlyphSet(
        channel_id=profile.channel_id,
        profile_id=profile.profile_id,
        patches=patches,
        source_frame_count=len(samples),
        notes=notes,
        created_at=created_at or datetime.now(UTC),
    )


def glyphs_to_dict(glyphs: DigitGlyphSet) -> dict[str, Any]:
    return {
        "schema_version": glyphs.schema_version,
        "channel_id": glyphs.channel_id,
        "profile_id": glyphs.profile_id,
        "created_at": glyphs.created_at.isoformat(),
        "notes": glyphs.notes,
        "source_frame_count": glyphs.source_frame_count,
        "glyph_width": GLYPH_WIDTH,
        "glyph_height": GLYPH_HEIGHT,
        "digits": {
            str(digit): glyphs.patches[digit].tolist() for digit in range(10)
        },
    }


def glyphs_from_dict(data: Any) -> DigitGlyphSet:
    if not isinstance(data, dict):
        raise GlyphSchemaError(f"Glyphs must be a JSON object, got {type(data).__name__}")
    if data.get("schema_version") != GLYPH_SCHEMA_VERSION:
        raise GlyphSchemaError(
            f"Unsupported glyph schema_version {data.get('schema_version')!r}; "
            f"this build reads version {GLYPH_SCHEMA_VERSION}."
        )
    created_at = datetime.fromisoformat(data["created_at"])
    digits = data.get("digits")
    if not isinstance(digits, Mapping):
        raise GlyphSchemaError("digits must be an object keyed by 0-9.")
    patches: dict[int, np.ndarray] = {}
    for key, raw in digits.items():
        try:
            digit = int(key)
        except (TypeError, ValueError) as exc:
            raise GlyphSchemaError(f"Invalid digit key {key!r}") from exc
        try:
            patch = np.asarray(raw, dtype=np.uint8)
        except (TypeError, ValueError) as exc:
            raise GlyphSchemaError(f"Digit {digit} patch is not numeric: {exc}") from exc
        patches[digit] = patch
    if data.get("glyph_width") != GLYPH_WIDTH or data.get("glyph_height") != GLYPH_HEIGHT:
        raise GlyphSchemaError(
            f"Glyph grid must be {GLYPH_WIDTH}x{GLYPH_HEIGHT}, got "
            f"{data.get('glyph_width')}x{data.get('glyph_height')}."
        )
    return DigitGlyphSet(
        schema_version=data["schema_version"],
        channel_id=data["channel_id"],
        profile_id=data["profile_id"],
        patches=patches,
        source_frame_count=data.get("source_frame_count", 0),
        notes=data.get("notes", ""),
        created_at=created_at,
    )


def save_glyphs(glyphs: DigitGlyphSet, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(glyphs_to_dict(glyphs), indent=2, ensure_ascii=False)
    destination.write_text(payload + "\n", encoding="utf-8")
    return destination


def load_glyphs(path: str | Path, *, expect_channel: str) -> DigitGlyphSet:
    glyphs = load_glyphs_unchecked(path)
    glyphs.assert_channel(expect_channel)
    return glyphs


def load_glyphs_unchecked(path: str | Path) -> DigitGlyphSet:
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise GlyphSchemaError(f"Could not read glyphs at {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GlyphSchemaError(f"{source} is not valid JSON: {exc}") from exc
    return glyphs_from_dict(data)
