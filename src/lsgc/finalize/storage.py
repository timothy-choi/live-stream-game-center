"""JSON persistence for finalization profiles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lsgc.calibration.errors import ProfileSchemaError
from lsgc.calibration.geometry import Region
from lsgc.finalize.profile import SCHEMA_VERSION, FinalizationProfile


def _region_to_dict(region: Region) -> dict[str, int]:
    return {"x": region.x, "y": region.y, "width": region.width, "height": region.height}


def _region_from_dict(data: Any, where: str) -> Region:
    if not isinstance(data, dict):
        raise ProfileSchemaError(f"{where} must be an object, got {type(data).__name__}")
    missing = [key for key in ("x", "y", "width", "height") if key not in data]
    if missing:
        raise ProfileSchemaError(f"{where} is missing {', '.join(missing)}")
    return Region(x=data["x"], y=data["y"], width=data["width"], height=data["height"])


def profile_to_dict(profile: FinalizationProfile) -> dict[str, Any]:
    return {
        "schema_version": profile.schema_version,
        "kind": "finalization",
        "profile_id": profile.profile_id,
        "channel_id": profile.channel_id,
        "revision": profile.revision,
        "created_at": profile.created_at.isoformat(),
        "notes": profile.notes,
        "reference_width": profile.reference_width,
        "reference_height": profile.reference_height,
        "completion_panel": _region_to_dict(profile.completion_panel),
        "left_final_score_region": _region_to_dict(profile.left_final_score_region),
        "right_final_score_region": _region_to_dict(profile.right_final_score_region),
        "stable_anchors": [_region_to_dict(r) for r in profile.stable_anchors],
        "dynamic_ignore": [_region_to_dict(r) for r in profile.dynamic_ignore],
        "presence_threshold": profile.presence_threshold,
        "reader_min_score": profile.reader_min_score,
        "reader_min_margin": profile.reader_min_margin,
    }


def profile_from_dict(data: Any) -> FinalizationProfile:
    if not isinstance(data, dict):
        raise ProfileSchemaError(
            f"Finalization profile must be a JSON object, got {type(data).__name__}"
        )
    if "schema_version" not in data:
        raise ProfileSchemaError("Finalization profile is missing schema_version.")
    version = data["schema_version"]
    if version != SCHEMA_VERSION:
        raise ProfileSchemaError(
            f"Unsupported finalization schema_version {version!r}; "
            f"this build reads version {SCHEMA_VERSION}."
        )
    kind = data.get("kind")
    if kind not in (None, "finalization"):
        raise ProfileSchemaError(f"Unexpected profile kind {kind!r}; expected 'finalization'.")
    required = (
        "profile_id",
        "channel_id",
        "revision",
        "created_at",
        "reference_width",
        "reference_height",
        "completion_panel",
        "left_final_score_region",
        "right_final_score_region",
        "stable_anchors",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ProfileSchemaError(
            f"Finalization profile is missing required fields: {', '.join(missing)}"
        )
    try:
        created_at = datetime.fromisoformat(data["created_at"])
    except (TypeError, ValueError) as exc:
        raise ProfileSchemaError(
            f"created_at is not a valid ISO 8601 timestamp: {exc}"
        ) from exc
    for key in ("stable_anchors", "dynamic_ignore"):
        if key in data and not isinstance(data[key], list):
            raise ProfileSchemaError(f"{key} must be a list.")
    try:
        return FinalizationProfile(
            schema_version=version,
            profile_id=data["profile_id"],
            channel_id=data["channel_id"],
            revision=data["revision"],
            created_at=created_at,
            notes=data.get("notes", ""),
            reference_width=data["reference_width"],
            reference_height=data["reference_height"],
            completion_panel=_region_from_dict(data["completion_panel"], "completion_panel"),
            left_final_score_region=_region_from_dict(
                data["left_final_score_region"], "left_final_score_region"
            ),
            right_final_score_region=_region_from_dict(
                data["right_final_score_region"], "right_final_score_region"
            ),
            stable_anchors=tuple(
                _region_from_dict(r, f"stable_anchors[{i}]")
                for i, r in enumerate(data["stable_anchors"])
            ),
            dynamic_ignore=tuple(
                _region_from_dict(r, f"dynamic_ignore[{i}]")
                for i, r in enumerate(data.get("dynamic_ignore", []))
            ),
            presence_threshold=float(data.get("presence_threshold", 0.55)),
            reader_min_score=float(data.get("reader_min_score", 0.80)),
            reader_min_margin=float(data.get("reader_min_margin", 0.08)),
        )
    except TypeError as exc:
        raise ProfileSchemaError(
            f"Finalization profile fields have the wrong types: {exc}"
        ) from exc


def save_finalization_profile(profile: FinalizationProfile, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile_to_dict(profile), indent=2, ensure_ascii=False)
    destination.write_text(payload + "\n", encoding="utf-8")
    return destination


def load_finalization_profile(path: str | Path, *, expect_channel: str) -> FinalizationProfile:
    profile = load_finalization_profile_unchecked(path)
    profile.assert_channel(expect_channel)
    return profile


def load_finalization_profile_unchecked(path: str | Path) -> FinalizationProfile:
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileSchemaError(f"Could not read finalization profile at {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileSchemaError(f"{source} is not valid JSON: {exc}") from exc
    return profile_from_dict(data)
