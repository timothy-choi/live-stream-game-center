"""Presentation routing: text-first when calibrated, otherwise logo.

Does not look at video ids, titles, timestamps, or expected matchups.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from lsgc.calibration.profile import CalibrationProfile, IdentityPresentation
from lsgc.identity.logos import LogoRecognizer, extract_identity_crops
from lsgc.identity.models import IdentityMethod, TeamIdentityObservation, UnreadableIdentityReason
from lsgc.identity.text import UnvalidatedTextReader, resolve_normalized_text

TextReader = Callable[..., TeamIdentityObservation]


class IdentityRecognizer:
    """Per-side identity from a calibrated crop."""

    def __init__(
        self,
        profile: CalibrationProfile,
        *,
        logos: LogoRecognizer | None = None,
        text_reader: TextReader | None = None,
        expect_channel: str | None = None,
    ) -> None:
        if expect_channel is not None:
            profile.assert_channel(expect_channel)
        if logos is not None:
            logos.templates.assert_matches(profile)
        self.profile = profile
        self.logos = logos
        self.text_reader = text_reader or UnvalidatedTextReader().read_crop

    def recognize_side(
        self,
        crop: np.ndarray,
        presentation: IdentityPresentation,
    ) -> TeamIdentityObservation:
        if presentation & (IdentityPresentation.NAME | IdentityPresentation.ABBREVIATION):
            text_obs = self.text_reader(crop, presentation)
            if text_obs.readable:
                return text_obs
        if presentation & IdentityPresentation.LOGO:
            if self.logos is None:
                return TeamIdentityObservation.unknown(
                    UnreadableIdentityReason.NO_TEMPLATE, method=IdentityMethod.LOGO
                )
            return self.logos.read_crop(crop)
        if not (presentation & (IdentityPresentation.NAME | IdentityPresentation.ABBREVIATION)):
            return TeamIdentityObservation.unknown(UnreadableIdentityReason.NO_TEMPLATE)
        return text_obs  # type: ignore[possibly-undefined]

    def recognize_image(
        self, image: np.ndarray
    ) -> tuple[TeamIdentityObservation, TeamIdentityObservation]:
        left_crop, right_crop = extract_identity_crops(image, self.profile)
        left = self.recognize_side(left_crop, self.profile.left_identity_presentation)
        right = self.recognize_side(right_crop, self.profile.right_identity_presentation)
        return left, right
