"""Channel-bound logo templates and conservative template matching.

Method (chosen after measuring identity crops on the Arcade fixture):

1. Crop the calibrated identity region (no full-frame search).
2. Resize to a 32x24 grayscale descriptor (area interpolation).
3. Score each template with zero-normalised correlation.
4. Gate with mean-BGR cosine similarity (team panels are strongly coloured).
5. Combined score = min(ZNCC, color cosine). Reject unless the winner clears
   min_score and beats the next team by min_margin.

Construction/tuning on this overlay: true matches ≥ ~0.87, false ≤ ~0.53,
true margins ≥ ~0.34. Defaults 0.70 / 0.15 sit in that gap. Heuristic
similarity is not a calibrated probability.

Runtime matching never reads video ids, titles, timestamps, or validation
manifests. Provenance on disk is documentation only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from lsgc.calibration.profile import CalibrationProfile
from lsgc.identity.catalog import DEFAULT_CATALOG, TeamCatalog
from lsgc.identity.errors import LogoMismatchError, LogoSchemaError
from lsgc.identity.models import IdentityMethod, TeamIdentityObservation, UnreadableIdentityReason
from lsgc.score.glyphs import zero_normalised_correlation
from lsgc.score.preprocess import extract_region

LOGO_SCHEMA_VERSION = 1
LOGO_WIDTH = 32
LOGO_HEIGHT = 24
DEFAULT_MIN_SCORE = 0.70
DEFAULT_MIN_MARGIN = 0.15


@dataclass(frozen=True)
class LogoTemplate:
    team_id: str
    patch: np.ndarray
    """LOGO_HEIGHT x LOGO_WIDTH uint8 grayscale."""

    mean_bgr: tuple[float, float, float]
    """Mean BGR of the construction crop. Color gate only."""


@dataclass(frozen=True)
class LogoTemplateSet:
    channel_id: str
    profile_id: str
    templates: tuple[LogoTemplate, ...]
    min_score: float = DEFAULT_MIN_SCORE
    min_margin: float = DEFAULT_MIN_MARGIN
    source_template_count: int = 0
    created_at: datetime | None = None
    notes: str = ""
    schema_version: int = LOGO_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(UTC))
        if self.schema_version != LOGO_SCHEMA_VERSION:
            raise LogoSchemaError(
                f"Unsupported logo schema_version {self.schema_version}; "
                f"this build reads version {LOGO_SCHEMA_VERSION}."
            )
        frozen: list[LogoTemplate] = []
        for item in self.templates:
            patch = np.ascontiguousarray(item.patch)
            if patch.shape != (LOGO_HEIGHT, LOGO_WIDTH) or patch.dtype != np.uint8:
                raise LogoSchemaError(
                    f"Logo patch for {item.team_id} must be "
                    f"{LOGO_HEIGHT}x{LOGO_WIDTH} uint8, got {patch.shape} {patch.dtype}"
                )
            frozen.append(
                LogoTemplate(
                    team_id=item.team_id, patch=patch, mean_bgr=tuple(item.mean_bgr)
                )
            )
        object.__setattr__(self, "templates", tuple(frozen))

    def assert_channel(self, channel_id: str) -> None:
        if self.channel_id != channel_id:
            raise LogoMismatchError(
                f"Logo templates belong to channel {self.channel_id!r}, not {channel_id!r}."
            )

    def assert_matches(self, profile: CalibrationProfile) -> None:
        self.assert_channel(profile.channel_id)
        if self.profile_id != profile.profile_id:
            raise LogoMismatchError(
                f"Logo templates belong to profile {self.profile_id!r}, "
                f"not {profile.profile_id!r}."
            )

    @property
    def supported_team_ids(self) -> frozenset[str]:
        return frozenset(item.team_id for item in self.templates)


def descriptor_from_crop(crop: np.ndarray) -> np.ndarray:
    if crop is None or crop.size == 0 or crop.ndim != 3:
        return np.zeros((LOGO_HEIGHT, LOGO_WIDTH), dtype=np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (LOGO_WIDTH, LOGO_HEIGHT), interpolation=cv2.INTER_AREA)


def mean_bgr(crop: np.ndarray) -> tuple[float, float, float]:
    if crop is None or crop.size == 0:
        return (0.0, 0.0, 0.0)
    mean = crop.reshape(-1, 3).mean(axis=0)
    return float(mean[0]), float(mean[1]), float(mean[2])


def color_cosine(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom < 1e-6:
        return 0.0
    return float(np.clip(np.dot(va, vb) / denom, -1.0, 1.0))


class LogoRecognizer:
    """Maps one identity crop to a TeamIdentityObservation using templates."""

    def __init__(
        self,
        templates: LogoTemplateSet,
        *,
        catalog: TeamCatalog | None = None,
        min_score: float | None = None,
        min_margin: float | None = None,
        expect_channel: str | None = None,
    ) -> None:
        if expect_channel is not None:
            templates.assert_channel(expect_channel)
        self.templates = templates
        self.catalog = catalog or DEFAULT_CATALOG
        self.min_score = templates.min_score if min_score is None else min_score
        self.min_margin = templates.min_margin if min_margin is None else min_margin

    def read_crop(self, crop: np.ndarray) -> TeamIdentityObservation:
        if crop is None or crop.size == 0 or crop.ndim != 3 or min(crop.shape[:2]) < 2:
            return TeamIdentityObservation.unknown(UnreadableIdentityReason.EMPTY_CROP)
        if not self.templates.templates:
            return TeamIdentityObservation.unknown(
                UnreadableIdentityReason.NO_TEMPLATE, method=IdentityMethod.LOGO
            )
        probe = descriptor_from_crop(crop)
        color = mean_bgr(crop)
        best: dict[str, float] = {}
        for tmpl in self.templates.templates:
            zncc = zero_normalised_correlation(probe, tmpl.patch)
            combined = min(zncc, color_cosine(color, tmpl.mean_bgr))
            previous = best.get(tmpl.team_id, -1.0)
            if combined > previous:
                best[tmpl.team_id] = combined
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        winner_id, winner_score = ranked[0]
        runner_id, runner_score = (ranked[1] if len(ranked) > 1 else (None, -1.0))
        margin = winner_score - runner_score
        if winner_score < self.min_score:
            return TeamIdentityObservation.unknown(
                UnreadableIdentityReason.LOW_CONFIDENCE,
                method=IdentityMethod.LOGO,
                confidence=winner_score,
                margin=margin,
                runner_up_team_id=runner_id,
            )
        if margin < self.min_margin:
            return TeamIdentityObservation.unknown(
                UnreadableIdentityReason.AMBIGUOUS,
                method=IdentityMethod.LOGO,
                confidence=winner_score,
                margin=margin,
                runner_up_team_id=runner_id,
            )
        if self.catalog.get(winner_id) is None:
            return TeamIdentityObservation.unknown(
                UnreadableIdentityReason.NO_TEMPLATE,
                method=IdentityMethod.LOGO,
                confidence=winner_score,
                margin=margin,
            )
        return TeamIdentityObservation.known(
            winner_id,
            method=IdentityMethod.LOGO,
            confidence=winner_score,
            margin=margin,
            runner_up_team_id=runner_id,
        )


def build_logo_templates(
    profile: CalibrationProfile,
    samples: Sequence[tuple[np.ndarray, str]],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    notes: str = "",
) -> LogoTemplateSet:
    """Average-free: one stored patch per construction crop (side-specific)."""
    if not samples:
        raise LogoSchemaError("logo construction needs at least one labelled crop")
    items: list[LogoTemplate] = []
    for crop, team_id in samples:
        team_id = str(team_id)
        if DEFAULT_CATALOG.get(team_id) is None:
            raise LogoSchemaError(f"construction team_id {team_id!r} is not in the catalog")
        items.append(
            LogoTemplate(
                team_id=team_id,
                patch=descriptor_from_crop(crop),
                mean_bgr=mean_bgr(crop),
            )
        )
    return LogoTemplateSet(
        channel_id=profile.channel_id,
        profile_id=profile.profile_id,
        templates=tuple(items),
        min_score=min_score,
        min_margin=min_margin,
        source_template_count=len(items),
        notes=notes,
    )


def extract_identity_crops(
    image: np.ndarray, profile: CalibrationProfile
) -> tuple[np.ndarray, np.ndarray]:
    height, width = image.shape[:2]
    fitted = profile.for_frame_size(width, height)
    left = extract_region(image, fitted.left_identity, pad=0)
    right = extract_region(image, fitted.right_identity, pad=0)
    return left, right


def templates_to_dict(templates: LogoTemplateSet) -> dict[str, Any]:
    return {
        "schema_version": templates.schema_version,
        "channel_id": templates.channel_id,
        "profile_id": templates.profile_id,
        "descriptor_width": LOGO_WIDTH,
        "descriptor_height": LOGO_HEIGHT,
        "min_score": templates.min_score,
        "min_margin": templates.min_margin,
        "source_template_count": templates.source_template_count,
        "created_at": (templates.created_at or datetime.now(UTC)).isoformat(),
        "notes": templates.notes,
        "templates": [
            {
                "team_id": item.team_id,
                "mean_bgr": [round(v, 4) for v in item.mean_bgr],
                "patch": item.patch.tolist(),
            }
            for item in templates.templates
        ],
    }


def templates_from_dict(data: Any) -> LogoTemplateSet:
    if not isinstance(data, dict):
        raise LogoSchemaError("Logo template document must be an object")
    if data.get("schema_version") != LOGO_SCHEMA_VERSION:
        raise LogoSchemaError(
            f"Unsupported logo schema_version {data.get('schema_version')!r}"
        )
    items = []
    for raw in data.get("templates") or []:
        items.append(
            LogoTemplate(
                team_id=str(raw["team_id"]),
                patch=np.asarray(raw["patch"], dtype=np.uint8),
                mean_bgr=tuple(float(v) for v in raw["mean_bgr"]),
            )
        )
    created = data.get("created_at")
    created_at = datetime.fromisoformat(created) if created else datetime.now(UTC)
    return LogoTemplateSet(
        channel_id=str(data["channel_id"]),
        profile_id=str(data["profile_id"]),
        templates=tuple(items),
        min_score=float(data.get("min_score", DEFAULT_MIN_SCORE)),
        min_margin=float(data.get("min_margin", DEFAULT_MIN_MARGIN)),
        source_template_count=int(data.get("source_template_count") or len(items)),
        created_at=created_at,
        notes=str(data.get("notes") or ""),
    )


def save_logo_templates(templates: LogoTemplateSet, path: Path) -> None:
    path.write_text(json.dumps(templates_to_dict(templates), indent=2) + "\n", encoding="utf-8")


def load_logo_templates_unchecked(path: Path) -> LogoTemplateSet:
    return templates_from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_logo_templates(path: Path, *, expect_channel: str) -> LogoTemplateSet:
    templates = load_logo_templates_unchecked(path)
    templates.assert_channel(expect_channel)
    return templates
