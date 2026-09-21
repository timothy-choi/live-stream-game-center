"""Developer diagnostic: draw calibration geometry over a representative image.

Development tooling, not product UI. It draws rectangles and labels; it reads
nothing from the image, so no OCR or detection is involved.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lsgc.calibration.errors import CalibrationValidationError
from lsgc.calibration.geometry import Region
from lsgc.calibration.profile import CalibrationProfile

ARTIFACT_DIR = Path("artifacts/calibration")
"""Under the gitignored artifacts tree; generated images are never committed."""

# BGR, matching the frame layout the decoder produces.
_COLOURS = {
    "scoreboard": (0, 255, 0),
    "score": (0, 0, 255),
    "identity": (0, 200, 255),
    "anchor": (255, 255, 0),
    "ignore": (255, 0, 255),
}


def _draw(image: np.ndarray, region: Region, colour: tuple[int, int, int], label: str,
          *, thickness: int, label_below: bool) -> None:
    # right/bottom are exclusive, so step back a pixel to draw on the last
    # pixel the region actually covers.
    cv2.rectangle(
        image, (region.x, region.y), (region.right - 1, region.bottom - 1), colour, thickness
    )
    baseline = region.bottom + 11 if label_below else region.y - 4
    baseline = max(9, min(baseline, image.shape[0] - 2))
    cv2.putText(
        image, label, (region.x, baseline), cv2.FONT_HERSHEY_PLAIN, 0.8, colour, 1, cv2.LINE_AA
    )


def render_overlay(image: np.ndarray, profile: CalibrationProfile) -> np.ndarray:
    """Return a copy of `image` annotated with the profile's regions.

    If the image size differs from the profile's reference size, the profile is
    rescaled first, which also means a mismatched aspect ratio is rejected
    rather than drawn in the wrong place.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise CalibrationValidationError(
            f"Expected an HxWx3 BGR image, got shape {image.shape}"
        )

    height, width = image.shape[:2]
    fitted = profile.for_frame_size(width, height)

    canvas = image.copy()
    _draw(canvas, fitted.scoreboard, _COLOURS["scoreboard"], "scoreboard",
          thickness=2, label_below=False)
    _draw(canvas, fitted.left_score, _COLOURS["score"], "L score",
          thickness=1, label_below=False)
    _draw(canvas, fitted.right_score, _COLOURS["score"], "R score",
          thickness=1, label_below=False)

    left_kinds = ",".join(fitted.left_identity_presentation.to_names())
    right_kinds = ",".join(fitted.right_identity_presentation.to_names())
    _draw(canvas, fitted.left_identity, _COLOURS["identity"], f"L ident [{left_kinds}]",
          thickness=1, label_below=True)
    _draw(canvas, fitted.right_identity, _COLOURS["identity"], f"R ident [{right_kinds}]",
          thickness=1, label_below=True)

    for index, anchor in enumerate(fitted.stable_anchors):
        _draw(canvas, anchor, _COLOURS["anchor"], f"anchor{index}",
              thickness=1, label_below=True)
    for index, ignored in enumerate(fitted.dynamic_ignore):
        _draw(canvas, ignored, _COLOURS["ignore"], f"ign{index}",
              thickness=1, label_below=True)

    return canvas


def save_overlay(
    image: np.ndarray, profile: CalibrationProfile, path: str | Path
) -> Path:
    """Render an overlay and write it to `path`."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), render_overlay(image, profile)):
        raise CalibrationValidationError(f"Could not write overlay image to {destination}")
    return destination
