"""Run the detector over the manually labelled M3 samples and report metrics.

Needs frames captured by `tools/m3_fixture.py capture`, which are gitignored,
so this is run by hand and never by pytest.

    python tools/m3_validate.py [--frames DIR] [--threshold T] [--sweep]

Metrics here are evidence about this fixture set on one channel. They are not
an estimate of production accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lsgc.calibration import load_profile  # noqa: E402
from lsgc.vision import (  # noqa: E402
    DEFAULT_THRESHOLD,
    ScoreboardDetector,
    load_reference,
    save_detection,
)

MANIFEST = Path("tests/fixtures/m3_validation_set.json")
PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
REFERENCE = Path("tests/fixtures/m3_reference.json")


def evaluate(frames: Path, threshold: float, overlays: bool) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text())
    channel = manifest["channel_id"]
    profile = load_profile(PROFILE, expect_channel=channel)
    reference = load_reference(REFERENCE, expect_channel=channel)
    detector = ScoreboardDetector(
        profile, reference, threshold=threshold, expect_channel=channel
    )

    rows = []
    for sample in manifest["samples"]:
        offset = sample["offset_s"]
        path = frames / f"t{offset:08.3f}.png"
        image = cv2.imread(str(path))
        if image is None:
            raise SystemExit(
                f"missing {path}\nRun: python tools/m3_fixture.py capture"
            )
        result = detector.detect(image)
        rows.append({
            "offset_s": offset,
            "state": sample["state"],
            "expected": sample["scoreboard_present"],
            "predicted": result.present,
            "score": result.score,
            "anchors": [a.score for a in result.anchors],
            "notes": sample.get("notes", ""),
        })
        if overlays:
            save_detection(image, result, Path("artifacts/detection") / f"t{offset:08.3f}.png")
    return rows


def report(rows: list[dict], threshold: float) -> int:
    tp = sum(r["expected"] and r["predicted"] for r in rows)
    tn = sum(not r["expected"] and not r["predicted"] for r in rows)
    fp = sum(not r["expected"] and r["predicted"] for r in rows)
    fn = sum(r["expected"] and not r["predicted"] for r in rows)
    total = len(rows)

    print(f"\nthreshold = {threshold:.2f}, {total} samples\n")
    print("                     predicted present   predicted absent")
    print(f"  actual present     {tp:>17}   {fn:>16}")
    print(f"  actual absent      {fp:>17}   {tn:>16}")

    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    print(f"\n  TP={tp}  TN={tn}  FP={fp}  FN={fn}")
    print(f"  accuracy={accuracy:.4f}  PRESENT precision={precision:.4f}  "
          f"PRESENT recall={recall:.4f}")

    pos = sorted(r["score"] for r in rows if r["expected"])
    neg = sorted(r["score"] for r in rows if not r["expected"])
    print(f"\n  positive scores n={len(pos)}: min={pos[0]:+.3f} p10={np.percentile(pos,10):+.3f} "
          f"median={np.median(pos):+.3f} max={pos[-1]:+.3f}")
    print(f"  negative scores n={len(neg)}: min={neg[0]:+.3f} "
          f"median={np.median(neg):+.3f} max={neg[-1]:+.3f}")
    print(f"  separation: weakest positive {pos[0]:+.3f} vs strongest negative "
          f"{neg[-1]:+.3f}  ->  gap {pos[0]-neg[-1]:+.3f}")

    closest_pos = min((r for r in rows if r["expected"]), key=lambda r: r["score"])
    closest_neg = max((r for r in rows if not r["expected"]), key=lambda r: r["score"])
    print(f"\n  closest positive: t{closest_pos['offset_s']} ({closest_pos['state']}) "
          f"score={closest_pos['score']:+.3f}")
    print(f"  closest negative: t{closest_neg['offset_s']} ({closest_neg['state']}) "
          f"score={closest_neg['score']:+.3f}")

    wrong = [r for r in rows if r["expected"] != r["predicted"]]
    if wrong:
        print(f"\n  MISCLASSIFIED ({len(wrong)}):")
        for r in wrong:
            kind = "false positive" if r["predicted"] else "false negative"
            print(f"    t{r['offset_s']} {r['state']}: {kind}, score={r['score']:+.3f}")
    else:
        print("\n  MISCLASSIFIED: none")

    print("\n  hard negatives:")
    for r in rows:
        if not r["expected"] and "banner" in r["state"]:
            verdict = "REJECTED" if not r["predicted"] else "*** ACCEPTED (false positive) ***"
            print(f"    t{r['offset_s']:<6} {r['state']:<32} score={r['score']:+.3f}  {verdict}")

    print("\nThese are fixture-validation metrics on one channel's calibration, "
          "not an estimate of production accuracy.")
    return 1 if wrong else 0


def sweep(rows: list[dict]) -> None:
    print("\nthreshold sweep:")
    print("   thr     TP  TN  FP  FN   accuracy")
    for thr in [x / 20 for x in range(-4, 20)]:
        tp = sum(r["expected"] and r["score"] >= thr for r in rows)
        tn = sum(not r["expected"] and r["score"] < thr for r in rows)
        fp = sum(not r["expected"] and r["score"] >= thr for r in rows)
        fn = sum(r["expected"] and r["score"] < thr for r in rows)
        flag = "  <- perfect" if fp == 0 and fn == 0 else ""
        print(f"  {thr:+.2f}   {tp:>2}  {tn:>2}  {fp:>2}  {fn:>2}   "
              f"{(tp+tn)/len(rows):.4f}{flag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, default=Path("artifacts/m3"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--overlays", action="store_true", help="write diagnostic images")
    args = parser.parse_args(argv)

    rows = evaluate(args.frames, args.threshold, args.overlays)
    code = report(rows, args.threshold)
    if args.sweep:
        sweep(rows)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
