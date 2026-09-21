"""Bounded text-identity path.

The Arcade gameplay scoreboard in the current fixture is LOGO-only, so this
module is the exact-alias mapper plus a crop reader that does not invent OCR.
It never fuzzy-matches. Real name/abbreviation OCR remains unvalidated.
"""

from __future__ import annotations

from lsgc.calibration.profile import IdentityPresentation
from lsgc.identity.catalog import DEFAULT_CATALOG, TeamCatalog, normalize_identity_text
from lsgc.identity.models import IdentityMethod, TeamIdentityObservation, UnreadableIdentityReason


def resolve_normalized_text(
    text: str,
    catalog: TeamCatalog | None = None,
    *,
    presentation: IdentityPresentation | None = None,
) -> TeamIdentityObservation:
    """Map already-extracted text to a canonical team. No fuzzy matching."""
    catalog = catalog or DEFAULT_CATALOG
    normalized = normalize_identity_text(text)
    if not normalized:
        return TeamIdentityObservation.unknown(UnreadableIdentityReason.EMPTY_CROP)
    team_id = catalog.resolve_text(normalized)
    if team_id is None:
        return TeamIdentityObservation.unknown(
            UnreadableIdentityReason.AMBIGUOUS,
            method=_method_for(presentation, normalized),
            confidence=0.0,
        )
    return TeamIdentityObservation.known(
        team_id,
        method=_method_for(presentation, normalized),
        confidence=1.0,
        margin=1.0,
    )


class UnvalidatedTextReader:
    """Default crop reader: no OCR. Returns UNKNOWN so logo fallback can run."""

    def read_crop(self, crop, presentation: IdentityPresentation) -> TeamIdentityObservation:
        return TeamIdentityObservation.unknown(
            UnreadableIdentityReason.TEXT_UNVALIDATED,
            method=_method_for(presentation, ""),
        )


def _method_for(
    presentation: IdentityPresentation | None, normalized: str
) -> IdentityMethod:
    if presentation is not None and presentation & IdentityPresentation.ABBREVIATION:
        if len(normalized) <= 4:
            return IdentityMethod.TEXT_ABBREVIATION
    if presentation is not None and presentation & IdentityPresentation.NAME:
        return IdentityMethod.TEXT_NAME
    if len(normalized) <= 4:
        return IdentityMethod.TEXT_ABBREVIATION
    return IdentityMethod.TEXT_NAME
