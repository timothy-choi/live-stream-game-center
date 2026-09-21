"""Held-out M10 identity validation on archived frames. Not run by pytest.

    python tools/m10_validate.py
    python tools/m10_validate.py --postgres   # persist/restart if LSGC_TEST_DATABASE_URL
    python tools/m10_validate.py --1080       # 1080p subset only

Needs gitignored artifacts from M3/M5 captures. No internet.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from lsgc.calibration import load_profile
from lsgc.identity import (
    IdentityRecognizer,
    LogoRecognizer,
    MatchupConfirmationPolicy,
    MatchupObservationPipeline,
    TemporalMatchupValidator,
    load_logo_templates,
)
from lsgc.models import Frame
from datetime import UTC, datetime
from lsgc.vision import ScoreboardDetector, load_reference

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "m10_identity_validation_set.json"
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"
# Labelled M3/M5 offsets are tens of seconds apart. The live default 8s
# candidate gap would reset between labels; that gap is for the 2s worker
# cadence, not this convenience sample. Same split M7 used for sparse replay.
SPARSE_POLICY = MatchupConfirmationPolicy(
    required_observations=2, min_span_s=2.0, max_candidate_gap_s=None
)
LIVE_POLICY = MatchupConfirmationPolicy()


@dataclass
class SideStat:
    labelled: int = 0
    correct: int = 0
    incorrect: int = 0
    unknown: int = 0

    @property
    def accuracy_all(self) -> float:
        return self.correct / self.labelled if self.labelled else 0.0

    @property
    def accuracy_readable(self) -> float:
        readable = self.correct + self.incorrect
        return self.correct / readable if readable else 0.0

    @property
    def coverage(self) -> float:
        readable = self.correct + self.incorrect
        return readable / self.labelled if self.labelled else 0.0


def _load_image(directory: Path, offset: float):
    path = directory / f"t{offset:08.3f}.png"
    image = cv2.imread(str(path))
    return path, image


def _frame(image, index: int, offset: float, source_id: str) -> Frame:
    return Frame(
        index=index,
        image=image,
        width=image.shape[1],
        height=image.shape[0],
        timestamp_s=float(offset),
        source_url=f"file://{source_id}",
        received_at=datetime.now(tz=UTC),
        source_id=source_id,
        extra={"source_epoch": 1},
    )


def _update_side(stat: SideStat, pred, expected: str | None) -> None:
    if expected is None:
        return
    stat.labelled += 1
    if not pred.readable:
        stat.unknown += 1
    elif pred.team_id == expected:
        stat.correct += 1
    else:
        stat.incorrect += 1


def _live_cadence_confirm(directory: Path, rec: dict, pipeline) -> dict | None:
    """Default 2s/8s policy on held-out 2s-apart frames (not construction 40)."""
    offsets = (42.0, 44.0)
    validator = TemporalMatchupValidator(LIVE_POLICY)
    expected = (rec["left_team_id"], rec["right_team_id"])
    used = 0
    for index, offset in enumerate(offsets):
        path = directory / f"t{offset:08.3f}.png"
        if not path.is_file():
            return None
        image = cv2.imread(str(path))
        frame = _frame(image, index, offset, rec["key"])
        observation = pipeline.observe_frame(frame)
        if observation is None or not observation.is_valid_complete:
            return {"offsets": offsets, "confirmed": None, "reason": "incomplete"}
        result = validator.apply(observation)
        used += 1
        if result.outcome.value == "confirmed":
            return {
                "offsets": offsets,
                "confirmed": result.state.pair,
                "observations": used,
                "false": result.state.pair != expected,
            }
    return {
        "offsets": offsets,
        "confirmed": validator.state.pair,
        "observations": used,
        "false": validator.state.pair not in (None, expected),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M10 held-out identity validation")
    parser.add_argument("--1080", dest="only_1080", action="store_true")
    parser.add_argument("--postgres", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    profile = load_profile(ROOT / manifest["profile"], expect_channel=CHANNEL)
    reference = load_reference(ROOT / manifest["reference"], expect_channel=CHANNEL)
    logos = load_logo_templates(ROOT / manifest["logos"], expect_channel=CHANNEL)
    detector = ScoreboardDetector(profile, reference)
    recognizer = IdentityRecognizer(profile, logos=LogoRecognizer(logos))
    pipeline = MatchupObservationPipeline(detector, recognizer)

    overall = SideStat()
    per_team: dict[str, SideStat] = {}
    per_rec: dict[str, dict] = {}
    gating = {"absent_total": 0, "absent_gated": 0, "absent_leaked": 0}
    frame_rows = []

    recordings = manifest["recordings"]
    if args.only_1080:
        recordings = [item for item in recordings if item["key"].endswith("1080")]

    for rec in recordings:
        directory = ROOT / rec["directory"]
        rec_stat = SideStat()
        both = partial = wrong = unknown_both = 0
        validator = TemporalMatchupValidator(SPARSE_POLICY)
        confirm_at = None
        conflicts = 0
        observations_used = 0
        if not directory.is_dir():
            print(f"MISSING directory {directory}")
            continue
        for sample_i, sample in enumerate(rec["samples"]):
            offset = sample["offset_s"]
            path, image = _load_image(directory, offset)
            if image is None:
                print(f"MISSING {path}")
                continue
            frame = _frame(image, sample_i, offset, rec["key"])
            observation = pipeline.observe_frame(frame)
            expected_present = bool(sample["scoreboard_present"])
            if not expected_present:
                gating["absent_total"] += 1
                if observation is None:
                    gating["absent_gated"] += 1
                else:
                    gating["absent_leaked"] += 1
                validator.note_absence(
                    frame_index=frame.index,
                    timestamp_s=frame.timestamp_s,
                    source_epoch=1,
                )
                continue
            if observation is None:
                left_pred = right_pred = None
                left_id = right_id = None
            else:
                left_pred, right_pred = observation.left, observation.right
                left_id, right_id = left_pred.team_id, right_pred.team_id
                result = validator.apply(observation)
                observations_used += 1
                if result.outcome.value == "confirmed" and confirm_at is None:
                    confirm_at = {
                        "offset_s": offset,
                        "observations": observations_used,
                        "pair": result.state.pair,
                        "latency_s": offset - rec["samples"][0]["offset_s"],
                    }
                if result.outcome.value == "conflict":
                    conflicts += 1
            for team_id, pred in (
                (sample["left_team_id"], left_pred),
                (sample["right_team_id"], right_pred),
            ):
                if team_id is None:
                    continue
                per_team.setdefault(team_id, SideStat())
                if pred is None:
                    overall.labelled += 1
                    overall.unknown += 1
                    rec_stat.labelled += 1
                    rec_stat.unknown += 1
                    per_team[team_id].labelled += 1
                    per_team[team_id].unknown += 1
                else:
                    _update_side(overall, pred, team_id)
                    _update_side(rec_stat, pred, team_id)
                    _update_side(per_team[team_id], pred, team_id)
            left_ok = left_pred is not None and left_pred.readable and left_pred.team_id == sample["left_team_id"]
            right_ok = right_pred is not None and right_pred.readable and right_pred.team_id == sample["right_team_id"]
            left_bad = left_pred is not None and left_pred.readable and left_pred.team_id != sample["left_team_id"]
            right_bad = right_pred is not None and right_pred.readable and right_pred.team_id != sample["right_team_id"]
            if left_ok and right_ok:
                both += 1
            elif left_bad or right_bad:
                wrong += 1
            elif (left_pred is None or not left_pred.readable) and (
                right_pred is None or not right_pred.readable
            ):
                unknown_both += 1
            else:
                partial += 1
            frame_rows.append(
                {
                    "key": rec["key"],
                    "offset_s": offset,
                    "expected": f"{sample['left_team_id']}/{sample['right_team_id']}",
                    "pred": f"{left_id}/{right_id}",
                }
            )
        per_rec[rec["key"]] = {
            "pair": f"{rec['left_team_id']}/{rec['right_team_id']}",
            "side": rec_stat.__dict__,
            "accuracy_all": rec_stat.accuracy_all,
            "both_correct": both,
            "partial": partial,
            "any_wrong": wrong,
            "both_unknown": unknown_both,
            "confirmed": validator.state.pair,
            "confirm_at": confirm_at,
            "conflicts": conflicts,
            "false_confirmation": (
                validator.state.pair is not None
                and validator.state.pair != (rec["left_team_id"], rec["right_team_id"])
            ),
            "live_cadence": _live_cadence_confirm(
                directory, rec, pipeline
            ),
        }

        if args.postgres and validator.state.initialized:
            from lsgc.persist import DurableMatchupTracker, apply_migrations, connect
            from lsgc.persist.config import postgres_test_dsn

            dsn = postgres_test_dsn()
            if not dsn:
                print("postgres requested but LSGC_TEST_DATABASE_URL unset")
            else:
                conn = connect(dsn)
                apply_migrations(conn)
                conn.commit()
                from lsgc.persist import PostgresGameRepository

                store = PostgresGameRepository(conn)
                tracker = DurableMatchupTracker.open(
                    store,
                    external_broadcast_id=f"m10-{rec['key']}",
                    policy=SPARSE_POLICY,
                )
                for sample_i, sample in enumerate(rec["samples"]):
                    if not sample["scoreboard_present"]:
                        continue
                    path, image = _load_image(directory, sample["offset_s"])
                    if image is None:
                        continue
                    frame = _frame(image, sample_i, sample["offset_s"], rec["key"])
                    observation = pipeline.observe_frame(frame)
                    if observation is None:
                        tracker.note_absence(
                            frame_index=frame.index,
                            timestamp_s=frame.timestamp_s,
                            source_epoch=1,
                        )
                    else:
                        tracker.apply(observation)
                persisted = tracker.game
                restored = DurableMatchupTracker.open(
                    store, external_broadcast_id=f"m10-{rec['key']}"
                )
                per_rec[rec["key"]]["persisted"] = persisted.matchup_state().pair
                per_rec[rec["key"]]["restored"] = restored.validator.state.pair
                conn.close()

    print("=== M10 held-out identity ===")
    print(
        f"sides labelled={overall.labelled} correct={overall.correct} "
        f"incorrect={overall.incorrect} UNKNOWN={overall.unknown}"
    )
    print(
        f"accuracy_all={overall.accuracy_all:.3f} "
        f"accuracy_readable={overall.accuracy_readable:.3f} "
        f"coverage={overall.coverage:.3f}"
    )
    print("per team:")
    for team_id, stat in sorted(per_team.items()):
        print(
            f"  {team_id}: n={stat.labelled} correct={stat.correct} "
            f"incorrect={stat.incorrect} UNKNOWN={stat.unknown}"
        )
    print("per recording:")
    for key, info in per_rec.items():
        print(
            f"  {key}: expected={info['pair']} confirmed={info['confirmed']} "
            f"both={info['both_correct']} partial={info['partial']} "
            f"wrong={info['any_wrong']} unk={info['both_unknown']} "
            f"conflicts={info['conflicts']} false={info['false_confirmation']}"
        )
        if info.get("confirm_at"):
            print(f"    confirm_at={info['confirm_at']}")
        if info.get("live_cadence"):
            print(f"    live_cadence={info['live_cadence']}")
        if info.get("persisted"):
            print(f"    persisted={info['persisted']} restored={info['restored']}")
    print(
        f"gating absent={gating['absent_total']} gated={gating['absent_gated']} "
        f"leaked={gating['absent_leaked']}"
    )
    mistakes = [row for row in frame_rows if row["pred"] != row["expected"] and "None" not in row["pred"]]
    if mistakes:
        print("incorrect readable frames:")
        for row in mistakes[:20]:
            print(f"  {row}")
    out = ROOT / "artifacts" / "m10" / "held_out_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "overall": overall.__dict__,
                "per_team": {k: v.__dict__ for k, v in per_team.items()},
                "per_recording": per_rec,
                "gating": gating,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    print(f"wrote {out}")
    false_conf = any(info["false_confirmation"] for info in per_rec.values())
    leaked = gating["absent_leaked"] > 0
    return 1 if false_conf or leaked or overall.incorrect else 0


if __name__ == "__main__":
    raise SystemExit(main())
