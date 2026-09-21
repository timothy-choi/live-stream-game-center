"""Inspect identity crops and compare simple logo-matching methods.

Not part of pytest. Writes gitignored crops under artifacts/m10/.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from lsgc.calibration import load_profile
from lsgc.calibration.geometry import Region
from lsgc.score.glyphs import zero_normalised_correlation
from lsgc.score.preprocess import extract_region

PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"
OUT = Path("artifacts/m10/inspect")

SAMPLES = [
    ("giants", "artifacts/m3", 240, "hBblAZD-ApY 7-7"),
    ("giants", "artifacts/m3", 1500, "hBblAZD-ApY 49-28"),
    ("chargers", "artifacts/m5/chargers", 40, "LAC @ SF early"),
    ("chargers", "artifacts/m5/chargers", 600, "LAC @ SF mid"),
    ("packers", "artifacts/m5/packers", 40, "SF @ GB early"),
    ("packers", "artifacts/m5/packers", 600, "SF @ GB mid"),
    ("raiders", "artifacts/m5/raiders", 40, "LV @ SF early"),
    ("raiders", "artifacts/m5/raiders", 600, "LV @ SF mid"),
    ("vikings", "artifacts/m5/vikings", 40, "MIN @ SF early"),
    ("vikings", "artifacts/m5/vikings", 600, "MIN @ SF mid"),
]


def _descriptor(crop: np.ndarray, size: tuple[int, int] = (32, 24)) -> np.ndarray:
    if crop.size == 0:
        return np.zeros((size[1], size[0]), dtype=np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)


def _sobel(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    mag = mag / (mag.max() or 1.0)
    return (mag * 255).astype(np.uint8)


def _mean_bgr(crop: np.ndarray) -> tuple[float, float, float]:
    if crop.size == 0:
        return (0.0, 0.0, 0.0)
    mean = crop.reshape(-1, 3).mean(axis=0)
    return float(mean[0]), float(mean[1]), float(mean[2])


def main() -> None:
    profile = load_profile(PROFILE, expect_channel=CHANNEL)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for key, directory, offset, note in SAMPLES:
        path = Path(directory) / f"t{offset:08.3f}.png"
        image = cv2.imread(str(path))
        if image is None:
            print(f"MISSING {path}")
            continue
        h, w = image.shape[:2]
        fitted = profile.for_frame_size(w, h)
        left = extract_region(image, fitted.left_identity, pad=0)
        right = extract_region(image, fitted.right_identity, pad=0)
        cv2.imwrite(str(OUT / f"{key}_{offset}_L.png"), left)
        cv2.imwrite(str(OUT / f"{key}_{offset}_R.png"), right)
        rows.append(
            {
                "key": key,
                "offset": offset,
                "note": note,
                "left_bgr": _mean_bgr(left),
                "right_bgr": _mean_bgr(right),
                "left_shape": left.shape[:2],
                "right_shape": right.shape[:2],
                "left_desc": _descriptor(left),
                "right_desc": _descriptor(right),
            }
        )
        lb, lg, lr = _mean_bgr(left)
        rb, rg, rr = _mean_bgr(right)
        print(
            f"{key:8} t={offset:5} L bgr=({lb:6.1f},{lg:6.1f},{lr:6.1f}) "
            f"R bgr=({rb:6.1f},{rg:6.1f},{rr:6.1f}) {note}"
        )

    print("\n# grayscale ZNCC left vs left (first of each key)")
    first = {}
    for row in rows:
        first.setdefault(row["key"] + "_L", row)
    keys = list(dict.fromkeys(r["key"] for r in rows))
    print("      ", " ".join(f"{k[:7]:>7}" for k in keys))
    for a in keys:
        scores = []
        for b in keys:
            scores.append(
                zero_normalised_correlation(
                    first[a + "_L"]["left_desc"], first[b + "_L"]["left_desc"]
                )
            )
        print(f"{a[:7]:>7}", " ".join(f"{s:7.3f}" for s in scores))

    print("\n# grayscale ZNCC right vs right")
    print("      ", " ".join(f"{k[:7]:>7}" for k in keys))
    for a in keys:
        scores = []
        for b in keys:
            scores.append(
                zero_normalised_correlation(
                    first[a + "_L"]["right_desc"], first[b + "_L"]["right_desc"]
                )
            )
        print(f"{a[:7]:>7}", " ".join(f"{s:7.3f}" for s in scores))


if __name__ == "__main__":
    main()
