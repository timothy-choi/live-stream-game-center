"""Run the score reader over the manually labelled M6 samples and report metrics.

Needs frames already captured for M3/M5 (`tools/m3_fixture.py capture` and
`tools/m5_fixture.py capture`). Those PNGs are gitignored, so this is run by
hand and never by pytest.

    python tools/m6_validate.py
    python tools/m6_validate.py --1080

Metrics here are evidence about this fixture set on one channel. They are not
an estimate of production accuracy. A wrong numeric read is counted separately
from an unreadable result.

Construction-set frames (the nine hBblAZD-ApY offsets that built the glyphs)
are scored for sanity but excluded from held-out totals.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lsgc.calibration import load_profile  # noqa: E402
from lsgc.score import (  # noqa: E402
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    ScoreObservationPipeline,
    ScoreReader,
    load_glyphs,
)
from lsgc.vision import ScoreboardDetector, load_reference  # noqa: E402

MANIFEST = Path("tests/fixtures/m6_score_validation_set.json")
PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
REFERENCE = Path("tests/fixtures/m3_reference.json")
GLYPHS = Path("tests/fixtures/m6_digit_glyphs.json")


def _side_bucket(pred, expected: int) -> str:
    if pred is None:
        return "unreadable"
    if pred == expected:
        return "exact"
    return "incorrect"


def evaluate(
    *,
    min_score: float,
    min_margin: float,
    include_construction: bool,
) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    channel = manifest["channel_id"]
    profile = load_profile(PROFILE, expect_channel=channel)
    reference = load_reference(REFERENCE, expect_channel=channel)
    glyphs = load_glyphs(GLYPHS, expect_channel=channel)
    pipeline = ScoreObservationPipeline(
        ScoreboardDetector(profile, reference, expect_channel=channel),
        ScoreReader(glyphs, min_score=min_score, min_margin=min_margin),
    )

    rows: list[dict] = []
    for rec in manifest["recordings"]:
        frames = Path(rec["frames_dir"])
        for sample in rec["samples"]:
            if sample["role"] == "glyph_construction" and not include_construction:
                continue
            offset = sample["offset_s"]
            path = frames / f"t{offset:08.3f}.png"
            image = cv2.imread(str(path))
            if image is None:
                raise SystemExit(
                    f"missing {path}\n"
                    "Run: python tools/m3_fixture.py capture\n"
                    "     python tools/m5_fixture.py capture"
                )
            observation = pipeline.observe(image)
            if observation is None:
                left_pred = right_pred = None
                left_reason = right_reason = "detector_absent"
                det_score = None
            else:
                left_pred = observation.left.value
                right_pred = observation.right.value
                left_reason = (
                    None
                    if observation.left.readable
                    else observation.left.reason.value
                )
                right_reason = (
                    None
                    if observation.right.readable
                    else observation.right.reason.value
                )
                det_score = observation.detection_score
            rows.append(
                {
                    "key": rec["key"],
                    "video_id": rec["video_id"],
                    "matchup": rec["matchup"],
                    "offset_s": offset,
                    "role": sample["role"],
                    "left_expected": sample["left_score"],
                    "right_expected": sample["right_score"],
                    "left_pred": left_pred,
                    "right_pred": right_pred,
                    "left_reason": left_reason,
                    "right_reason": right_reason,
                    "detection_score": det_score,
                }
            )
    return rows


def _side_stats(rows: list[dict], side: str) -> dict[str, int]:
    counts = Counter(
        _side_bucket(r[f"{side}_pred"], r[f"{side}_expected"]) for r in rows
    )
    return {
        "n": len(rows),
        "exact": counts["exact"],
        "incorrect": counts["incorrect"],
        "unreadable": counts["unreadable"],
    }


def _frame_stats(rows: list[dict]) -> dict[str, int]:
    both_exact = 0
    partial = 0
    any_wrong = 0
    both_unreadable = 0
    for row in rows:
        lb = _side_bucket(row["left_pred"], row["left_expected"])
        rb = _side_bucket(row["right_pred"], row["right_expected"])
        if lb == "exact" and rb == "exact":
            both_exact += 1
        if (lb == "exact") != (rb == "exact"):
            partial += 1
        if lb == "incorrect" or rb == "incorrect":
            any_wrong += 1
        if lb == "unreadable" and rb == "unreadable":
            both_unreadable += 1
    return {
        "n": len(rows),
        "both_exact": both_exact,
        "partial": partial,
        "any_wrong": any_wrong,
        "both_unreadable": both_unreadable,
    }


def _print_side(title: str, stats: dict[str, int]) -> None:
    n = stats["n"]
    exact, incorrect, unread = stats["exact"], stats["incorrect"], stats["unreadable"]
    readable = exact + incorrect
    cov = readable / n if n else 0.0
    acc_r = exact / readable if readable else float("nan")
    print(
        f"  {title:<8} n={n:3d}  exact={exact:3d}  incorrect={incorrect:3d}  "
        f"unreadable={unread:3d}  coverage={cov:.3f}  acc_readable={acc_r:.3f}"
    )


def report(rows: list[dict], *, min_score: float, min_margin: float) -> int:
    print(
        f"\nmin_score={min_score:.2f}  min_margin={min_margin:.2f}  "
        f"frames={len(rows)}\n"
    )
    keys = []
    for row in rows:
        if row["key"] not in keys:
            keys.append(row["key"])

    for key in keys:
        subset = [r for r in rows if r["key"] == key]
        pairs = sorted({(r["left_expected"], r["right_expected"]) for r in subset})
        print(
            f"{key}  {subset[0]['video_id']}  {subset[0]['matchup']}  "
            f"frames={len(subset)}  unique_scores={len(pairs)}"
        )
        _print_side("left", _side_stats(subset, "left"))
        _print_side("right", _side_stats(subset, "right"))
        fr = _frame_stats(subset)
        print(
            f"  frames   both_exact={fr['both_exact']}  partial={fr['partial']}  "
            f"any_wrong={fr['any_wrong']}  both_unreadable={fr['both_unreadable']}"
        )
        print()

    print("AGGREGATE")
    _print_side("left", _side_stats(rows, "left"))
    _print_side("right", _side_stats(rows, "right"))
    both = _side_stats(rows, "left")["n"] + _side_stats(rows, "right")["n"]
    exact = _side_stats(rows, "left")["exact"] + _side_stats(rows, "right")["exact"]
    incorrect = (
        _side_stats(rows, "left")["incorrect"] + _side_stats(rows, "right")["incorrect"]
    )
    unread = (
        _side_stats(rows, "left")["unreadable"] + _side_stats(rows, "right")["unreadable"]
    )
    readable = exact + incorrect
    print(
        f"  sides    n={both}  exact={exact}  incorrect={incorrect}  "
        f"unreadable={unread}  coverage={readable/both:.3f}  "
        f"acc_readable={exact/readable if readable else float('nan'):.3f}"
    )
    fr = _frame_stats(rows)
    print(
        f"  frames   n={fr['n']}  both_exact={fr['both_exact']}  "
        f"partial={fr['partial']}  any_wrong={fr['any_wrong']}  "
        f"both_unreadable={fr['both_unreadable']}"
    )

    wrongs = [
        r
        for r in rows
        if _side_bucket(r["left_pred"], r["left_expected"]) == "incorrect"
        or _side_bucket(r["right_pred"], r["right_expected"]) == "incorrect"
    ]
    if wrongs:
        print("\nINCORRECT NUMERIC READS")
        for r in wrongs:
            print(
                f"  {r['key']} t={r['offset_s']}  "
                f"L exp={r['left_expected']} pred={r['left_pred']}  "
                f"R exp={r['right_expected']} pred={r['right_pred']}"
            )
        return 1
    print("\nNo incorrect numeric reads.")
    return 0


def evaluate_1080() -> None:
    """Small scaled-resolution check on already-captured 1080p frames."""
    cases = [
        ("artifacts/m3_1080/t0780.000.png", 28, 21, "hBblAZD-ApY t=780"),
        ("artifacts/m3_1080/t1620.000.png", 56, 28, "hBblAZD-ApY t=1620"),
        ("artifacts/m5/geometry/vikings_1080_t0040.png", 0, 0, "UITXyHeI5KE t=40"),
        ("artifacts/m5/geometry/vikings_1080_t0600.png", 7, 28, "UITXyHeI5KE t=600"),
    ]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    channel = manifest["channel_id"]
    profile = load_profile(PROFILE, expect_channel=channel)
    reference = load_reference(REFERENCE, expect_channel=channel)
    glyphs = load_glyphs(GLYPHS, expect_channel=channel)
    pipeline = ScoreObservationPipeline(
        ScoreboardDetector(profile, reference, expect_channel=channel),
        ScoreReader(glyphs),
    )
    print("\n1080p scaled-resolution check (M2 geometry scaling, 720p glyphs)\n")
    for path, left_e, right_e, label in cases:
        image = cv2.imread(path)
        if image is None:
            print(f"  missing {path}")
            continue
        if image.shape[1] != 1920 or image.shape[0] != 1080:
            print(f"  skip {path}: shape {image.shape}")
            continue
        observation = pipeline.observe(image)
        if observation is None:
            print(f"  {label}: detector ABSENT")
            continue
        ok_l = observation.left.value == left_e
        ok_r = observation.right.value == right_e
        print(
            f"  {label}  L {observation.left.summary()} (exp {left_e} {'ok' if ok_l else 'NO'})  "
            f"R {observation.right.summary()} (exp {right_e} {'ok' if ok_r else 'NO'})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M6 labelled score-reading validation")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)
    parser.add_argument(
        "--include-construction",
        action="store_true",
        help="also score the nine glyph-construction frames (leaked; not held-out)",
    )
    parser.add_argument(
        "--1080",
        dest="check_1080",
        action="store_true",
        help="also run the existing 1920x1080 subset",
    )
    args = parser.parse_args()
    rows = evaluate(
        min_score=args.min_score,
        min_margin=args.min_margin,
        include_construction=args.include_construction,
    )
    status = report(rows, min_score=args.min_score, min_margin=args.min_margin)
    if args.check_1080:
        evaluate_1080()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
