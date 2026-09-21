"""Developer diagnostic: draw a detection result over the frame it came from.

Shows each anchor with its own score and whether it passed, so a surprising
verdict can be traced to the anchor responsible. Development tooling only.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lsgc.vision.detector import DetectionResult
from lsgc.vision.errors import VisionError

ARTIFACT_DIR = Path("artifacts/detection")
"""Under the gitignored artifacts tree; generated images are never committed."""

_PASS = (0, 220, 0)
_FAIL = (0, 0, 255)


def render_detection(image: np.ndarray, result: DetectionResult) -> np.ndarray:
    """Return a copy of `image` annotated with the verdict and per-anchor scores."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise VisionError(f"Expected an HxWx3 BGR image, got shape {image.shape}")
    if (image.shape[1], image.shape[0]) != (result.frame_width, result.frame_height):
        raise VisionError(
            f"Result describes a {result.frame_width}x{result.frame_height} frame, "
            f"but the image is {image.shape[1]}x{image.shape[0]}."
        )

    canvas = image.copy()
    for anchor in result.anchors:
        colour = _PASS if anchor.passed(result.threshold) else _FAIL
        region = anchor.region
        cv2.rectangle(
            canvas, (region.x, region.y), (region.right - 1, region.bottom - 1), colour, 2
        )
        label = f"a{anchor.index} {anchor.score:+.2f}"
        baseline = min(region.bottom + 12, canvas.shape[0] - 2)
        cv2.putText(
            canvas, label, (max(0, region.x - 6), baseline),
            cv2.FONT_HERSHEY_PLAIN, 0.9, colour, 1, cv2.LINE_AA,
        )

    verdict = "PRESENT" if result.present else "ABSENT"
    colour = _PASS if result.present else _FAIL
    banner = (
        f"{verdict}  score={result.score:+.3f}  thr={result.threshold:.2f}  "
        f"{result.profile_id} r{result.profile_revision}"
    )
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 26), (0, 0, 0), -1)
    cv2.putText(canvas, banner, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
    return canvas


def save_detection(image: np.ndarray, result: DetectionResult, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), render_detection(image, result)):
        raise VisionError(f"Could not write diagnostic image to {destination}")
    return destination
