"""Evaluate the existing M3 detector on the M5 cross-matchup set.

Needs frames under artifacts/m5/<key>/ captured by tools/m5_fixture.py.
Never run from pytest (YouTube + gitignored frames).

    python tools/m5_validate.py
    python tools/m5_validate.py --overlays

Uses the original M3 profile, reference, and threshold unchanged.
Offsets are fixture metadata only; they are not detector inputs.
"""

from __future__ import annotations

import argparse
import json
import statistics
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

MANIFEST = Path("tests/fixtures/m5_validation_set.json")
PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
REFERENCE = Path("tests/fixtures/m3_reference.json")
FRAME_ROOT = Path("artifacts/m5")


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else float("nan")


def _metrics(rows: list[dict]) -> dict:
    tp = sum(r["expected"] and r["predicted"] for r in rows)
    tn = sum(not r["expected"] and not r["predicted"] for r in rows)
    fp = sum(not r["expected"] and r["predicted"] for r in rows)
    fn = sum(r["expected"] and not r["predicted"] for r in rows)
    n = len(rows)
    pos = sorted(r["score"] for r in rows if r["expected"])
    neg = sorted(r["score"] for r in rows if not r["expected"])
    a0_pos = sorted(r["anchors"][0] for r in rows if r["expected"])
    a0_neg = sorted(r["anchors"][0] for r in rows if not r["expected"])
    a1_pos = sorted(r["anchors"][1] for r in rows if r["expected"])
    a1_neg = sorted(r["anchors"][1] for r in rows if not r["expected"])
    return {
        "n": n,
        "pos_n": len(pos),
        "neg_n": len(neg),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / n if n else float("nan"),
        "precision": tp / (tp + fp) if (tp + fp) else float("nan"),
        "recall": tp / (tp + fn) if (tp + fn) else float("nan"),
        "specificity": tn / (tn + fp) if (tn + fp) else float("nan"),
        "pos": pos,
        "neg": neg,
        "a0_pos": a0_pos,
        "a0_neg": a0_neg,
        "a1_pos": a1_pos,
        "a1_neg": a1_neg,
        "weakest_pos": min((r for r in rows if r["expected"]), key=lambda r: r["score"], default=None),
        "strongest_neg": max((r for r in rows if not r["expected"]), key=lambda r: r["score"], default=None),
        "wrong": [r for r in rows if r["expected"] != r["predicted"]],
    }


def _print_block(title: str, m: dict, threshold: float) -> None:
    print(f"\n=== {title} ===")
    print(f"n={m['n']}  pos={m['pos_n']}  neg={m['neg_n']}  threshold={threshold:.2f}")
    print(f"TP={m['tp']} TN={m['tn']} FP={m['fp']} FN={m['fn']}")
    print(
        f"accuracy={m['accuracy']:.4f}  precision={m['precision']:.4f}  "
        f"recall={m['recall']:.4f}  specificity={m['specificity']:.4f}"
    )
    if m["pos"]:
        print(
            f"combined POS min={m['pos'][0]:+.3f} median={_median(m['pos']):+.3f} "
            f"max={m['pos'][-1]:+.3f}"
        )
    if m["neg"]:
        print(
            f"combined NEG min={m['neg'][0]:+.3f} median={_median(m['neg']):+.3f} "
            f"max={m['neg'][-1]:+.3f}"
        )
        if m["pos"]:
            print(f"gap (weakest POS - strongest NEG) = {m['pos'][0] - m['neg'][-1]:+.3f}")
    if m["a0_pos"]:
        print(
            f"anchor0 POS min={m['a0_pos'][0]:+.3f} median={_median(m['a0_pos']):+.3f} "
            f"max={m['a0_pos'][-1]:+.3f}"
        )
    if m["a0_neg"]:
        print(
            f"anchor0 NEG min={m['a0_neg'][0]:+.3f} median={_median(m['a0_neg']):+.3f} "
            f"max={m['a0_neg'][-1]:+.3f}"
        )
    if m["a1_pos"]:
        print(
            f"anchor1 POS min={m['a1_pos'][0]:+.3f} median={_median(m['a1_pos']):+.3f} "
            f"max={m['a1_pos'][-1]:+.3f}"
        )
    if m["a1_neg"]:
        print(
            f"anchor1 NEG min={m['a1_neg'][0]:+.3f} median={_median(m['a1_neg']):+.3f} "
            f"max={m['a1_neg'][-1]:+.3f}"
        )
    wp, sn = m["weakest_pos"], m["strongest_neg"]
    if wp:
        print(
            f"weakest positive: t={wp['offset_s']} {wp['state']} "
            f"score={wp['score']:+.3f} a0={wp['anchors'][0]:+.3f} a1={wp['anchors'][1]:+.3f}"
        )
    if sn:
        print(
            f"strongest negative: t={sn['offset_s']} {sn['state']} "
            f"score={sn['score']:+.3f} a0={sn['anchors'][0]:+.3f} a1={sn['anchors'][1]:+.3f}"
        )
    if m["wrong"]:
        print(f"MISCLASSIFIED ({len(m['wrong'])}):")
        for r in m["wrong"]:
            kind = "FP" if r["predicted"] else "FN"
            print(
                f"  {kind} t={r['offset_s']} {r['state']} score={r['score']:+.3f} "
                f"a0={r['anchors'][0]:+.3f} a1={r['anchors'][1]:+.3f}  {r['notes']}"
            )
    else:
        print("MISCLASSIFIED: none")


def evaluate(threshold: float, overlays: bool) -> tuple[list[dict], dict[str, list[dict]]]:
    manifest = json.loads(MANIFEST.read_text())
    channel = manifest["channel_id"]
    profile = load_profile(PROFILE, expect_channel=channel)
    reference = load_reference(REFERENCE, expect_channel=channel)
    detector = ScoreboardDetector(
        profile, reference, threshold=threshold, expect_channel=channel
    )

    by_game: dict[str, list[dict]] = {}
    all_rows: list[dict] = []
    for rec in manifest["recordings"]:
        key = rec["key"]
        rows = []
        for sample in rec["samples"]:
            offset = sample["offset_s"]
            path = FRAME_ROOT / key / f"t{offset:08.3f}.png"
            image = cv2.imread(str(path))
            if image is None:
                raise SystemExit(f"missing {path}\nRun: python tools/m5_fixture.py capture")
            result = detector.detect(image)
            row = {
                "key": key,
                "video_id": rec["video_id"],
                "offset_s": offset,
                "state": sample["state"],
                "expected": sample["scoreboard_present"],
                "predicted": result.present,
                "score": result.score,
                "anchors": [a.score for a in result.anchors],
                "intensity": [a.intensity_similarity for a in result.anchors],
                "structure": [a.structure_similarity for a in result.anchors],
                "notes": sample.get("notes", ""),
            }
            rows.append(row)
            if overlays:
                dest = Path("artifacts/m5/detection") / key / f"t{offset:08.3f}.png"
                save_detection(image, result, dest)
        by_game[key] = rows
        all_rows.extend(rows)
    return all_rows, by_game


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--overlays", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text())
    all_rows, by_game = evaluate(args.threshold, args.overlays)
    titles = {r["key"]: r["title"] for r in manifest["recordings"]}
    for key, rows in by_game.items():
        _print_block(f"{key} — {titles[key]}", _metrics(rows), args.threshold)
    _print_block("AGGREGATE (four new matchups, original M3 fixture excluded)", _metrics(all_rows), args.threshold)
    return 1 if any(r["expected"] != r["predicted"] for r in all_rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
