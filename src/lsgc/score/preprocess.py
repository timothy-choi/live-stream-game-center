"""Deterministic score-crop extraction and digit localisation.

Preprocessing was chosen by looking at this channel's Arcade scoreboard, not
by stacking generic OCR tricks:

- The calibrated crop only (plus a 1px pad so anti-aliased edge pixels are
  not clipped). Coordinates always come from `CalibrationProfile.for_frame_size`.
- White ink is isolated as `min(B, G, R)`. Team panels are saturated colour,
  so they fail that test; the digits are near-white.
- 8x cubic upscaling, because native crops are ~59x26.
- Otsu on a slight blur, then a 3x3 close, for localisation only.
- Digit boxes from the column-projection of that mask. A single 0 is a wide
  oval whose hole is *not* treated as a gap; two digits are split only when
  the content is wide *and* the valley is nearly empty.

Recognition itself uses the grayscale (min-channel) patches, not the binary
mask, so anti-aliased 3/5/6/8 strokes keep their shape.
"""

from __future__ import annotations

import cv2
import numpy as np

from lsgc.calibration.geometry import Region
from lsgc.calibration.profile import CalibrationProfile

UPSCALE = 8
"""Native score crops are ~59x26; 8x cubic was enough to make digit holes and
gaps stable for projection-splitting. Further upscaling did not change
held-out reads."""

CROP_PAD_PX = 1
"""One pixel around the calibrated box. Absorbs anti-aliasing at the edge
without pulling in neighbouring chrome."""

SINGLE_DIGIT_MAX_ASPECT = 1.45
TWO_DIGIT_MIN_ASPECT = 1.70
"""Content bounding-box width/height. Measured on construction crops: a
single 0 sits around 1.2; two digits sit around 2.0. Between those, a deep
valley is required before splitting, because a 0's hole is a shallow dip."""

VALLEY_RATIO = 0.22
"""A real gap between digits has almost no ink. 0's interior hole still has
top and bottom strokes, so its column-sum valley stays well above this
fraction of the neighbouring peaks."""


def _require_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Expected an HxWx3 BGR image, got shape {image.shape}")
    return image


def extract_region(image: np.ndarray, region: Region, *, pad: int = CROP_PAD_PX) -> np.ndarray:
    """Crop `region` from a BGR image, padded and clipped to the frame."""
    image = _require_bgr(image)
    height, width = image.shape[:2]
    x0 = max(0, region.x - pad)
    y0 = max(0, region.y - pad)
    x1 = min(width, region.right + pad)
    y1 = min(height, region.bottom + pad)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0, 3), dtype=np.uint8)
    return image[y0:y1, x0:x1].copy()


def extract_score_crops(
    image: np.ndarray, profile: CalibrationProfile
) -> tuple[np.ndarray, np.ndarray, CalibrationProfile]:
    """Left and right score crops in the frame's own coordinates.

    The profile is rescaled with M2's deterministic rule. Crop coordinates
    are never hard-coded at 1280x720.
    """
    image = _require_bgr(image)
    height, width = image.shape[:2]
    fitted = profile.for_frame_size(width, height)
    left = extract_region(image, fitted.left_score)
    right = extract_region(image, fitted.right_score)
    return left, right, fitted


def min_channel(image: np.ndarray) -> np.ndarray:
    """Per-pixel min of B, G, R. White digits stay bright; coloured panels drop."""
    image = _require_bgr(image)
    return np.min(image, axis=2)


def upscale_gray(gray: np.ndarray, *, scale: int = UPSCALE) -> np.ndarray:
    if gray.size == 0:
        return gray
    return cv2.resize(
        gray,
        (gray.shape[1] * scale, gray.shape[0] * scale),
        interpolation=cv2.INTER_CUBIC,
    )


def localisation_mask(upscaled: np.ndarray) -> np.ndarray:
    """Binary mask used only to find digit boxes, not to recognise them."""
    if upscaled.size == 0:
        return upscaled
    blurred = cv2.GaussianBlur(upscaled, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (binary == 255).mean() > 0.5:
        binary = 255 - binary
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)


def digit_boxes(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Return 0–N boxes as (x0, y0, x1, y1) in mask coordinates."""
    if mask.size == 0:
        return []
    height, width = mask.shape
    row_ink = (mask > 0).sum(axis=1)
    ys = np.where(row_ink > 0.04 * width)[0]
    if ys.size == 0:
        return []
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    column = (mask[y0:y1] > 0).sum(axis=0).astype(np.float32)
    kernel = max(5, width // 30)
    if kernel % 2 == 0:
        kernel += 1
    smoothed = cv2.GaussianBlur(column.reshape(1, -1), (kernel, 1), 0).ravel()
    peak = float(smoothed.max())
    if peak <= 0:
        return []
    xs = np.where(smoothed > 0.08 * peak)[0]
    if xs.size == 0:
        return []
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    box_width = x1 - x0
    box_height = y1 - y0
    aspect = box_width / max(box_height, 1)

    def whole() -> list[tuple[int, int, int, int]]:
        return [(x0, y0, x1, y1)]

    if aspect < SINGLE_DIGIT_MAX_ASPECT:
        return whole()

    mid0 = x0 + int(0.28 * box_width)
    mid1 = x0 + int(0.72 * box_width)
    if mid1 <= mid0:
        return whole()
    valley = int(np.argmin(smoothed[mid0:mid1]) + mid0)
    left_peak = float(smoothed[x0:valley].max()) if valley > x0 else 0.0
    right_peak = float(smoothed[valley:x1].max()) if x1 > valley else 0.0
    valley_value = float(smoothed[valley])
    deep = (
        left_peak > 0
        and right_peak > 0
        and valley_value < VALLEY_RATIO * min(left_peak, right_peak)
    )
    if aspect >= TWO_DIGIT_MIN_ASPECT and deep:

        def trim(start: int, end: int) -> tuple[int, int, int, int] | None:
            band = smoothed[start:end]
            if band.size == 0 or float(band.max()) <= 0:
                return None
            nz = np.where(band > 0.08 * band.max())[0]
            if nz.size == 0:
                return None
            return start + int(nz[0]), y0, start + int(nz[-1]) + 1, y1

        left = trim(x0, valley)
        right = trim(valley, x1)
        if left is not None and right is not None:
            left_w = left[2] - left[0]
            right_w = right[2] - right[0]
            if left_w >= 0.18 * box_height and right_w >= 0.18 * box_height:
                return [left, right]
    return whole()


def contrast_stretch(patch: np.ndarray) -> np.ndarray:
    """Percentile stretch so glyph matching is insensitive to panel brightness."""
    if patch.size == 0:
        return patch
    low, high = np.percentile(patch, (2, 98))
    if high <= low + 1:
        return patch
    stretched = (patch.astype(np.float32) - float(low)) * (255.0 / float(high - low))
    return np.clip(stretched, 0, 255).astype(np.uint8)


def glyph_patch(
    upscaled: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
) -> np.ndarray | None:
    x0, y0, x1, y1 = box
    patch = upscaled[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    stretched = contrast_stretch(patch)
    return cv2.resize(stretched, (width, height), interpolation=cv2.INTER_AREA)
