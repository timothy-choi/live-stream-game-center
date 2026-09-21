"""JSON persistence for calibration profiles.

JSON because a profile is reviewed by humans and lives in version control:
output is stably ordered and indented so a recalibration produces a readable
diff. No database in this milestone.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lsgc.calibration.errors import ProfileSchemaError
from lsgc.calibration.geometry import Region
from lsgc.calibration.profile import SCHEMA_VERSION, CalibrationProfile, IdentityPresentation


def _region_to_dict(region: Region) -> dict[str, int]:
    return {"x": region.x, "y": region.y, "width": region.width, "height": region.height}


def _region_from_dict(data: Any, where: str) -> Region:
    if not isinstance(data, dict):
        raise ProfileSchemaError(f"{where} must be an object, got {type(data).__name__}")
    missing = [key for key in ("x", "y", "width", "height") if key not in data]
    if missing:
        raise ProfileSchemaError(f"{where} is missing {', '.join(missing)}")
    return Region(
        x=data["x"], y=data["y"], width=data["width"], height=data["height"]
    )


def profile_to_dict(profile: CalibrationProfile) -> dict[str, Any]:
    """Plain-data form of a profile, in a fixed, diff-friendly key order."""
    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "channel_id": profile.channel_id,
        "revision": profile.revision,
        "created_at": profile.created_at.isoformat(),
        "notes": profile.notes,
        "reference_width": profile.reference_width,
        "reference_height": profile.reference_height,
        "scoreboard": _region_to_dict(profile.scoreboard),
        "left_score": _region_to_dict(profile.left_score),
        "right_score": _region_to_dict(profile.right_score),
        "left_identity": _region_to_dict(profile.left_identity),
        "right_identity": _region_to_dict(profile.right_identity),
        "left_identity_presentation": profile.left_identity_presentation.to_names(),
        "right_identity_presentation": profile.right_identity_presentation.to_names(),
        "stable_anchors": [_region_to_dict(r) for r in profile.stable_anchors],
        "dynamic_ignore": [_region_to_dict(r) for r in profile.dynamic_ignore],
    }


def profile_from_dict(data: Any) -> CalibrationProfile:
    """Rebuild a profile, failing clearly on malformed or unsupported input."""
    if not isinstance(data, dict):
        raise ProfileSchemaError(f"Profile must be a JSON object, got {type(data).__name__}")

    if "schema_version" not in data:
        raise ProfileSchemaError("Profile is missing schema_version.")
    version = data["schema_version"]
    if version != SCHEMA_VERSION:
        raise ProfileSchemaError(
            f"Unsupported profile schema_version {version!r}; "
            f"this build reads version {SCHEMA_VERSION}."
        )

    required = (
        "profile_id", "channel_id", "revision", "created_at",
        "reference_width", "reference_height",
        "scoreboard", "left_score", "right_score", "left_identity", "right_identity",
        "left_identity_presentation", "right_identity_presentation", "stable_anchors",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ProfileSchemaError(f"Profile is missing required fields: {', '.join(missing)}")

    try:
        created_at = datetime.fromisoformat(data["created_at"])
    except (TypeError, ValueError) as exc:
        raise ProfileSchemaError(f"created_at is not a valid ISO 8601 timestamp: {exc}") from exc

    for key in ("stable_anchors", "dynamic_ignore"):
        if key in data and not isinstance(data[key], list):
            raise ProfileSchemaError(f"{key} must be a list.")

    try:
        return CalibrationProfile(
            schema_version=version,
            profile_id=data["profile_id"],
            channel_id=data["channel_id"],
            revision=data["revision"],
            created_at=created_at,
            notes=data.get("notes", ""),
            reference_width=data["reference_width"],
            reference_height=data["reference_height"],
            scoreboard=_region_from_dict(data["scoreboard"], "scoreboard"),
            left_score=_region_from_dict(data["left_score"], "left_score"),
            right_score=_region_from_dict(data["right_score"], "right_score"),
            left_identity=_region_from_dict(data["left_identity"], "left_identity"),
            right_identity=_region_from_dict(data["right_identity"], "right_identity"),
            left_identity_presentation=IdentityPresentation.parse(
                data["left_identity_presentation"]
            ),
            right_identity_presentation=IdentityPresentation.parse(
                data["right_identity_presentation"]
            ),
            stable_anchors=tuple(
                _region_from_dict(r, f"stable_anchors[{i}]")
                for i, r in enumerate(data["stable_anchors"])
            ),
            dynamic_ignore=tuple(
                _region_from_dict(r, f"dynamic_ignore[{i}]")
                for i, r in enumerate(data.get("dynamic_ignore", []))
            ),
        )
    except TypeError as exc:
        raise ProfileSchemaError(f"Profile fields have the wrong types: {exc}") from exc


def save_profile(profile: CalibrationProfile, path: str | Path) -> Path:
    """Write a profile as UTF-8 JSON with a trailing newline."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile_to_dict(profile), indent=2, ensure_ascii=False)
    destination.write_text(payload + "\n", encoding="utf-8")
    return destination


def load_profile(path: str | Path, *, expect_channel: str) -> CalibrationProfile:
    """Load a profile and bind it to the channel the caller intends to use.

    `expect_channel` is required rather than optional so runtime code cannot
    drift into treating a profile as a global Madden default. Tooling that
    genuinely needs to inspect a file without knowing its channel should call
    `load_profile_unchecked`, whose name makes that explicit.
    """
    profile = load_profile_unchecked(path)
    profile.assert_channel(expect_channel)
    return profile


def load_profile_unchecked(path: str | Path) -> CalibrationProfile:
    """Load without channel binding. For inspection tooling only."""
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileSchemaError(f"Could not read profile at {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileSchemaError(f"{source} is not valid JSON: {exc}") from exc
    return profile_from_dict(data)
