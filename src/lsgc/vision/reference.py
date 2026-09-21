"""Calibrated reference evidence: what normal stable structure looks like.

The division of responsibility matters:

- `CalibrationProfile` says WHERE to inspect (anchor rectangles).
- `ScoreboardReference` says WHAT those places look like when the calibrated
  scoreboard is on screen.

A reference holds one small grayscale patch per stable anchor, sampled from
representative frames of the channel being calibrated. Patches are downsampled
to a fixed grid, which keeps them compact enough to store as reviewable JSON
and doubles as the resolution-normalisation step: a 1080p frame's larger anchor
crop lands on the same grid as the 720p reference.

The patches describe structural chrome - panel dividers - chosen during
calibration. They carry no team, score, clock or timestamp information, and a
reference is bound to one channel's profile revision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from lsgc.calibration.geometry import Region
from lsgc.calibration.profile import CalibrationProfile
from lsgc.vision.errors import ReferenceMismatchError, ReferenceSchemaError

REFERENCE_SCHEMA_VERSION = 1
"""Version of the reference file format, independent of the profile's own
schema version and of the calibration revision."""

DESCRIPTOR_WIDTH = 12
DESCRIPTOR_HEIGHT = 24
"""Fixed descriptor grid, as (width, height) in pixels.

Chosen by measurement rather than taste: across the 40-sample M3 validation
set, separation between positives and negatives stops improving beyond roughly
this size (12x24 gives a 0.908 gap, 16x32 gives 0.914 for nearly twice the
data), while staying small enough that a whole reference is a few hundred
integers of reviewable JSON.
"""


def _descriptor(image: np.ndarray, region: Region) -> np.ndarray:
    """Grayscale, cropped to `region`, resampled onto the descriptor grid."""
    crop = image[region.y : region.bottom, region.x : region.right]
    if crop.size == 0:
        raise ReferenceMismatchError(
            f"Anchor {region} does not overlap a {image.shape[1]}x{image.shape[0]} frame."
        )
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return cv2.resize(
        gray, (DESCRIPTOR_WIDTH, DESCRIPTOR_HEIGHT), interpolation=cv2.INTER_AREA
    )


def _require_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim != 3 or image.shape[2] != 3:
        raise ReferenceMismatchError(f"Expected an HxWx3 BGR image, got shape {image.shape}")
    return image


@dataclass(frozen=True)
class AnchorReference:
    """Reference appearance of one stable anchor."""

    index: int
    """Position in the profile's `stable_anchors`, so the pairing is explicit."""

    region: Region
    """The anchor rectangle this was sampled from, in reference-frame
    coordinates. Stored so a profile edited after the reference was built is
    detected as stale instead of silently comparing the wrong pixels."""

    patch: np.ndarray
    """DESCRIPTOR_HEIGHT x DESCRIPTOR_WIDTH uint8 grayscale."""

    def __post_init__(self) -> None:
        if self.patch.shape != (DESCRIPTOR_HEIGHT, DESCRIPTOR_WIDTH):
            raise ReferenceSchemaError(
                f"Anchor {self.index} patch must be "
                f"{DESCRIPTOR_HEIGHT}x{DESCRIPTOR_WIDTH}, got {self.patch.shape}"
            )
        if self.patch.dtype != np.uint8:
            raise ReferenceSchemaError(
                f"Anchor {self.index} patch must be uint8, got {self.patch.dtype}"
            )


@dataclass(frozen=True)
class ScoreboardReference:
    """Reference evidence for one channel's calibrated scoreboard."""

    channel_id: str
    profile_id: str
    profile_revision: int
    """Bound to the exact calibration these patches were sampled against."""

    anchors: tuple[AnchorReference, ...]
    source_frame_count: int
    """How many representative frames were averaged, for provenance."""

    created_at: datetime = None  # type: ignore[assignment]
    notes: str = ""
    schema_version: int = REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(UTC))
        if not self.anchors:
            raise ReferenceSchemaError("Reference must describe at least one anchor.")
        if self.schema_version != REFERENCE_SCHEMA_VERSION:
            raise ReferenceSchemaError(
                f"Unsupported reference schema_version {self.schema_version}; "
                f"this build reads version {REFERENCE_SCHEMA_VERSION}."
            )

    def assert_matches(self, profile: CalibrationProfile) -> None:
        """Verify this reference was built for exactly this calibration.

        Checks channel, lineage, revision and anchor geometry. Any of these
        drifting means the stored patches no longer describe the pixels the
        profile points at.
        """
        if self.channel_id != profile.channel_id:
            raise ReferenceMismatchError(
                f"Reference belongs to channel {self.channel_id!r}, but the profile is "
                f"for {profile.channel_id!r}. Calibration and its reference evidence are "
                "per-channel."
            )
        if self.profile_id != profile.profile_id:
            raise ReferenceMismatchError(
                f"Reference was built for profile {self.profile_id!r}, "
                f"not {profile.profile_id!r}."
            )
        if self.profile_revision != profile.revision:
            raise ReferenceMismatchError(
                f"Reference was built for revision {self.profile_revision} of "
                f"{self.profile_id!r}, but the profile is revision {profile.revision}. "
                "Rebuild the reference after recalibrating."
            )
        if len(self.anchors) != len(profile.stable_anchors):
            raise ReferenceMismatchError(
                f"Reference describes {len(self.anchors)} anchors, "
                f"but the profile has {len(profile.stable_anchors)}."
            )
        for reference_anchor, profile_anchor in zip(self.anchors, profile.stable_anchors):
            if reference_anchor.region != profile_anchor:
                raise ReferenceMismatchError(
                    f"Anchor {reference_anchor.index} moved since the reference was built: "
                    f"reference {reference_anchor.region}, profile {profile_anchor}. "
                    "Rebuild the reference."
                )

    def summary(self) -> str:
        lines = [
            f"channel_id   {self.channel_id}",
            f"profile      {self.profile_id} r{self.profile_revision}",
            f"descriptor   {DESCRIPTOR_WIDTH}x{DESCRIPTOR_HEIGHT} grayscale per anchor",
            f"anchors      {len(self.anchors)}",
            f"built from   {self.source_frame_count} representative frame(s)",
            f"created_at   {self.created_at.isoformat()}",
        ]
        lines += [
            f"  anchor[{a.index}]  {a.region}  "
            f"mean={a.patch.mean():.1f} std={a.patch.std():.1f}"
            for a in self.anchors
        ]
        if self.notes:
            lines.append(f"notes        {self.notes}")
        return "\n".join(lines)


def build_reference(
    images: list[np.ndarray],
    profile: CalibrationProfile,
    *,
    notes: str = "",
    created_at: datetime | None = None,
) -> ScoreboardReference:
    """Sample reference patches from frames showing the normal scoreboard.

    Averaging several representative frames rather than trusting one keeps a
    single frame's transient content - a possession marker, a crowd shift -
    from being baked in as required structure. Every image must already show
    the calibrated scoreboard; nothing here verifies that, because deciding it
    is the detector's job and would be circular here.
    """
    if not images:
        raise ReferenceMismatchError("At least one representative frame is required.")

    stacks: list[list[np.ndarray]] = [[] for _ in profile.stable_anchors]
    for image in images:
        _require_bgr(image)
        height, width = image.shape[:2]
        fitted = profile.for_frame_size(width, height)
        for position, anchor in enumerate(fitted.stable_anchors):
            stacks[position].append(_descriptor(image, anchor).astype(np.float32))

    anchors = tuple(
        AnchorReference(
            index=position,
            region=anchor,
            patch=np.round(np.mean(stack, axis=0)).astype(np.uint8),
        )
        for position, (anchor, stack) in enumerate(zip(profile.stable_anchors, stacks))
    )
    return ScoreboardReference(
        channel_id=profile.channel_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        anchors=anchors,
        source_frame_count=len(images),
        notes=notes,
        created_at=created_at or datetime.now(UTC),
    )


def extract_descriptor(image: np.ndarray, region: Region) -> np.ndarray:
    """Descriptor for one region of a frame, as the detector sees it."""
    return _descriptor(_require_bgr(image), region)


# -- serialization ---------------------------------------------------------


def reference_to_dict(reference: ScoreboardReference) -> dict[str, Any]:
    """Plain data in a fixed key order, for diff-friendly JSON."""
    return {
        "schema_version": reference.schema_version,
        "channel_id": reference.channel_id,
        "profile_id": reference.profile_id,
        "profile_revision": reference.profile_revision,
        "created_at": reference.created_at.isoformat(),
        "notes": reference.notes,
        "source_frame_count": reference.source_frame_count,
        "descriptor_width": DESCRIPTOR_WIDTH,
        "descriptor_height": DESCRIPTOR_HEIGHT,
        "anchors": [
            {
                "index": anchor.index,
                "region": {
                    "x": anchor.region.x,
                    "y": anchor.region.y,
                    "width": anchor.region.width,
                    "height": anchor.region.height,
                },
                "patch": anchor.patch.tolist(),
            }
            for anchor in reference.anchors
        ],
    }


def reference_from_dict(data: Any) -> ScoreboardReference:
    if not isinstance(data, dict):
        raise ReferenceSchemaError(f"Reference must be a JSON object, got {type(data).__name__}")
    if "schema_version" not in data:
        raise ReferenceSchemaError("Reference is missing schema_version.")
    if data["schema_version"] != REFERENCE_SCHEMA_VERSION:
        raise ReferenceSchemaError(
            f"Unsupported reference schema_version {data['schema_version']!r}; "
            f"this build reads version {REFERENCE_SCHEMA_VERSION}."
        )

    required = ("channel_id", "profile_id", "profile_revision", "created_at", "anchors")
    missing = [key for key in required if key not in data]
    if missing:
        raise ReferenceSchemaError(f"Reference is missing required fields: {', '.join(missing)}")

    stored = (data.get("descriptor_width"), data.get("descriptor_height"))
    if stored != (DESCRIPTOR_WIDTH, DESCRIPTOR_HEIGHT):
        raise ReferenceSchemaError(
            f"Reference uses a {stored[0]}x{stored[1]} descriptor grid, but this build "
            f"compares on {DESCRIPTOR_WIDTH}x{DESCRIPTOR_HEIGHT}. Rebuild the reference."
        )

    try:
        created_at = datetime.fromisoformat(data["created_at"])
    except (TypeError, ValueError) as exc:
        raise ReferenceSchemaError(f"created_at is not a valid ISO 8601 timestamp: {exc}") from exc

    if not isinstance(data["anchors"], list):
        raise ReferenceSchemaError("anchors must be a list.")

    anchors = []
    for position, entry in enumerate(data["anchors"]):
        if not isinstance(entry, dict) or "patch" not in entry or "region" not in entry:
            raise ReferenceSchemaError(f"anchors[{position}] needs a region and a patch.")
        try:
            patch = np.asarray(entry["patch"], dtype=np.uint8)
        except (TypeError, ValueError) as exc:
            raise ReferenceSchemaError(f"anchors[{position}] patch is not numeric: {exc}") from exc
        region_data = entry["region"]
        if not isinstance(region_data, dict):
            raise ReferenceSchemaError(f"anchors[{position}] region must be an object.")
        try:
            region = Region(**region_data)
        except TypeError as exc:
            raise ReferenceSchemaError(f"anchors[{position}] region is malformed: {exc}") from exc
        anchors.append(
            AnchorReference(index=entry.get("index", position), region=region, patch=patch)
        )

    return ScoreboardReference(
        schema_version=data["schema_version"],
        channel_id=data["channel_id"],
        profile_id=data["profile_id"],
        profile_revision=data["profile_revision"],
        anchors=tuple(anchors),
        source_frame_count=data.get("source_frame_count", 0),
        notes=data.get("notes", ""),
        created_at=created_at,
    )


def save_reference(reference: ScoreboardReference, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(reference_to_dict(reference), indent=2, ensure_ascii=False)
    destination.write_text(payload + "\n", encoding="utf-8")
    return destination


def load_reference(path: str | Path, *, expect_channel: str) -> ScoreboardReference:
    """Load reference evidence, naming the channel it is meant for.

    Required rather than optional, matching `calibration.load_profile`: the
    channel boundary should be impossible to forget.
    """
    reference = load_reference_unchecked(path)
    if reference.channel_id != expect_channel:
        raise ReferenceMismatchError(
            f"Reference is for channel {reference.channel_id!r}, not {expect_channel!r}."
        )
    return reference


def load_reference_unchecked(path: str | Path) -> ScoreboardReference:
    """Load without channel binding. For inspection tooling only."""
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReferenceSchemaError(f"Could not read reference at {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReferenceSchemaError(f"{source} is not valid JSON: {exc}") from exc
    return reference_from_dict(data)
