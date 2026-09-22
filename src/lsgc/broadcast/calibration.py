"""Filesystem calibration lookup: one directory per exact channel id."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lsgc.calibration.profile import CalibrationProfile
from lsgc.calibration.storage import load_profile
from lsgc.finalize.profile import FinalizationProfile
from lsgc.finalize.storage import load_finalization_profile
from lsgc.identity.logos import LogoTemplateSet, load_logo_templates
from lsgc.score.glyphs import DigitGlyphSet, load_glyphs
from lsgc.vision.reference import ScoreboardReference, load_reference


_NON_PROFILE_JSON = {
    "reference.json",
    "glyphs.json",
    "logos.json",
    "finalization.json",
    "final_reference.json",
}


@dataclass(frozen=True)
class ChannelCalibration:
    profile: CalibrationProfile
    reference: ScoreboardReference
    glyphs: DigitGlyphSet
    logos: LogoTemplateSet | None = None
    finalization: FinalizationProfile | None = None
    final_reference: ScoreboardReference | None = None


class CalibrationLookup(Protocol):
    def get(self, channel_id: str) -> ChannelCalibration | None: ...


class MappingCalibrationLookup:
    """Test/dev lookup keyed by exact channel id."""

    def __init__(self, mapping: dict[str, ChannelCalibration] | None = None) -> None:
        self._mapping = dict(mapping or {})

    def get(self, channel_id: str) -> ChannelCalibration | None:
        return self._mapping.get(channel_id)

    def bind(self, channel_id: str, calibration: ChannelCalibration) -> None:
        self._mapping[channel_id] = calibration


class FilesystemCalibrationLookup:
    """`{root}/{channel_id}/profile.json` + `reference.json` + `glyphs.json`.

    Optional `logos.json` supplies identity templates. Missing logos is allowed:
    score tracking still starts; identity observations stay UNKNOWN.

    Highest revision wins when several profiles share one profile_id.
    Distinct profile_id values for the same channel are ambiguous: return None
    rather than picking one. Never fall back to another channel's directory.
    """

    def __init__(self, root: Path | None) -> None:
        self._root = root

    def get(self, channel_id: str) -> ChannelCalibration | None:
        if self._root is None or not channel_id:
            return None
        directory = self._root / channel_id
        if not directory.is_dir():
            return None
        loaded: list[CalibrationProfile] = []
        for path in sorted(directory.glob("*.json")):
            if path.name in _NON_PROFILE_JSON:
                continue
            try:
                loaded.append(load_profile(path, expect_channel=channel_id))
            except Exception:
                continue
        if not loaded:
            return None
        if len({item.profile_id for item in loaded}) > 1:
            return None
        profile = max(loaded, key=lambda item: (item.revision, item.profile_id))
        reference_path = directory / "reference.json"
        glyphs_path = directory / "glyphs.json"
        if not reference_path.is_file() or not glyphs_path.is_file():
            return None
        try:
            reference = load_reference(reference_path, expect_channel=channel_id)
            glyphs = load_glyphs(glyphs_path, expect_channel=channel_id)
        except Exception:
            return None
        logos = None
        logos_path = directory / "logos.json"
        if logos_path.is_file():
            try:
                logos = load_logo_templates(logos_path, expect_channel=channel_id)
                logos.assert_matches(profile)
            except Exception:
                return None
        finalization = None
        final_reference = None
        finalization_path = directory / "finalization.json"
        final_reference_path = directory / "final_reference.json"
        if finalization_path.is_file() and final_reference_path.is_file():
            try:
                finalization = load_finalization_profile(
                    finalization_path, expect_channel=channel_id
                )
                final_reference = load_reference(
                    final_reference_path, expect_channel=channel_id
                )
                final_reference.assert_matches(finalization)
            except Exception:
                finalization = None
                final_reference = None
        return ChannelCalibration(
            profile=profile,
            reference=reference,
            glyphs=glyphs,
            logos=logos,
            finalization=finalization,
            final_reference=final_reference,
        )
