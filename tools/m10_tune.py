"""Score construction-set identity templates against other frames.

Used to freeze min_score / min_margin before held-out evaluation.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lsgc.calibration import load_profile
from lsgc.score.glyphs import zero_normalised_correlation
from lsgc.score.preprocess import extract_region

PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"
SIZE = (32, 24)

# One construction crop per visual appearance. Held-out must exclude these.
CONSTRUCTION = [
    ("SF", "artifacts/m3", 240, "L"),
    ("NYG", "artifacts/m3", 240, "R"),
    ("LAC", "artifacts/m5/chargers", 40, "L"),
    ("SF", "artifacts/m5/chargers", 40, "R"),
    ("GB", "artifacts/m5/packers", 40, "R"),
    ("LV", "artifacts/m5/raiders", 40, "L"),
    ("MIN", "artifacts/m5/vikings", 40, "L"),
]

PROBES = [
    ("SF", "artifacts/m3", 1500, "L"),
    ("NYG", "artifacts/m3", 1500, "R"),
    ("SF", "artifacts/m3", 40, "L"),
    ("NYG", "artifacts/m3", 40, "R"),
    ("LAC", "artifacts/m5/chargers", 600, "L"),
    ("SF", "artifacts/m5/chargers", 600, "R"),
    ("SF", "artifacts/m5/packers", 600, "L"),
    ("GB", "artifacts/m5/packers", 600, "R"),
    ("LV", "artifacts/m5/raiders", 600, "L"),
    ("SF", "artifacts/m5/raiders", 600, "R"),
    ("MIN", "artifacts/m5/vikings", 600, "L"),
    ("SF", "artifacts/m5/vikings", 600, "R"),
    ("SF", "artifacts/m5/packers", 40, "L"),  # extra SF left vs giants construction
]


def desc(crop: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, SIZE, interpolation=cv2.INTER_AREA)


def color_vec(crop: np.ndarray) -> np.ndarray:
    mean = crop.reshape(-1, 3).mean(axis=0).astype(np.float32)
    norm = np.linalg.norm(mean) or 1.0
    return mean / norm


def color_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def load_crop(profile, directory, offset, side):
    image = cv2.imread(str(Path(directory) / f"t{offset:08.3f}.png"))
    h, w = image.shape[:2]
    fitted = profile.for_frame_size(w, h)
    region = fitted.left_identity if side == "L" else fitted.right_identity
    return extract_region(image, region, pad=0)


def main() -> None:
    profile = load_profile(PROFILE, expect_channel=CHANNEL)
    templates = []
    for team, directory, offset, side in CONSTRUCTION:
        crop = load_crop(profile, directory, offset, side)
        templates.append(
            {
                "team": team,
                "side": side,
                "gray": desc(crop),
                "color": color_vec(crop),
            }
        )

    true_gray, false_gray = [], []
    true_comb, false_comb = [], []
    true_margin, false_best = [], []
    for team, directory, offset, side in PROBES:
        crop = load_crop(profile, directory, offset, side)
        g = desc(crop)
        c = color_vec(crop)
        by_team: dict[str, float] = {}
        by_team_comb: dict[str, float] = {}
        for tmpl in templates:
            zncc = zero_normalised_correlation(g, tmpl["gray"])
            col = color_sim(c, tmpl["color"])
            comb = min(zncc, col)
            by_team[tmpl["team"]] = max(by_team.get(tmpl["team"], -1.0), zncc)
            by_team_comb[tmpl["team"]] = max(by_team_comb.get(tmpl["team"], -1.0), comb)
        ranked = sorted(by_team_comb.items(), key=lambda kv: kv[1], reverse=True)
        winner, win_s = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else -1.0
        margin = win_s - runner
        print(
            f"{team:3} {side} t={offset:4} pred={winner:3} "
            f"comb={win_s:+.3f} gray={by_team[team]:+.3f} "
            f"margin={margin:.3f} correct={winner == team}"
        )
        true_gray.append(by_team[team])
        true_comb.append(by_team_comb[team])
        true_margin.append(by_team_comb[team] - max(v for k, v in by_team_comb.items() if k != team))
        for other, score in by_team.items():
            if other != team:
                false_gray.append(score)
        for other, score in by_team_comb.items():
            if other != team:
                false_comb.append(score)

    print("\nTRUE gray", min(true_gray), np.median(true_gray), max(true_gray))
    print("FALSE gray", min(false_gray), np.median(false_gray), max(false_gray))
    print("TRUE comb", min(true_comb), np.median(true_comb), max(true_comb))
    print("FALSE comb", min(false_comb), np.median(false_comb), max(false_comb))
    print("TRUE margin", min(true_margin), np.median(true_margin), max(true_margin))


if __name__ == "__main__":
    main()
