"""Replay M6/M7 labelled sequences through the temporal validator.

    python tools/m7_validate.py
    python tools/m7_validate.py --dense

`--dense` needs frames from `python tools/m7_fixture.py capture` (YouTube).
The default sparse path uses M6 labelled timestamps as observation metadata
with max_candidate_gap disabled, because those labels are tens of seconds
apart by construction. That path measures confirmation on consecutive labelled
pairs, not live 2s cadence.

Dense path uses the default live policy (2 observations spanning 2s, 8s max
gap) on real M6 reads of 2s clusters.

Never run by pytest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lsgc.calibration import load_profile  # noqa: E402
from lsgc.score import ScoreObservationPipeline, ScoreReader, load_glyphs  # noqa: E402
from lsgc.score.models import RawScoreObservation, ScoreRead  # noqa: E402
from lsgc.track import ApplyOutcome, ConfirmationPolicy, TemporalScoreValidator  # noqa: E402
from lsgc.vision import ScoreboardDetector, load_reference  # noqa: E402

M6_MANIFEST = Path("tests/fixtures/m6_score_validation_set.json")
DENSE_MANIFEST = Path("tests/fixtures/m7_dense_replay.json")
PROFILE = Path("tests/fixtures/m1_fixture_profile.json")
REFERENCE = Path("tests/fixtures/m3_reference.json")
GLYPHS = Path("tests/fixtures/m6_digit_glyphs.json")


def _pair_obs(
    left: int,
    right: int,
    *,
    index: int,
    ts: float,
    epoch: int = 1,
    channel: str,
    profile_id: str,
) -> RawScoreObservation:
    def side(value: int) -> ScoreRead:
        return ScoreRead.of(value, confidence=0.99, margin=0.2, raw_digits=str(value))

    return RawScoreObservation(
        frame_index=index,
        timestamp_s=ts,
        received_at=None,
        left=side(left),
        right=side(right),
        detection_present=True,
        detection_score=0.99,
        detection_threshold=0.55,
        channel_id=channel,
        profile_id=profile_id,
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_epoch=epoch,
    )


def _summarize(name: str, validator: TemporalScoreValidator, visible_pairs: list[tuple[int, int]]) -> None:
    confirmed = [c.new_pair for c in validator.history]
    print(f"\n=== {name} ===")
    print(f"  observations={validator.observations_seen} absences={validator.absences_seen}")
    print(f"  initializations={validator.initializations} confirmed_changes={validator.confirmed_changes}")
    print(f"  candidate_starts={validator.candidate_starts} replacements={validator.candidate_replacements} resets={validator.candidate_resets}")
    print(f"  duplicates={validator.duplicates_ignored} stale={validator.stale_ignored}")
    print(f"  trusted={validator.state.summary()}")
    latencies = []
    for change in validator.history:
        if change.first_seen_timestamp_s is not None and change.confirmed_timestamp_s is not None:
            dt = change.confirmed_timestamp_s - change.first_seen_timestamp_s
            n = change.evidence_count
            latencies.append((change.summary(), dt, n))
            print(f"  {change.summary()}  latency_s={dt:g}  obs={n}")
        else:
            print(f"  {change.summary()}")
    false = [c for c in validator.history if c.new_pair not in set(visible_pairs)]
    if false:
        print(f"  FALSE TRUSTED TRANSITIONS: {false}")
    else:
        print("  false trusted transitions: 0 (confirmed pairs were all in the visible sample set)")
    return None


def sparse_replay() -> int:
    """M6 labels as observations; gap restart disabled (sparse sampling)."""
    manifest = json.loads(M6_MANIFEST.read_text(encoding="utf-8"))
    policy = ConfirmationPolicy(
        required_observations=2,
        min_span_s=2.0,
        max_candidate_gap_s=None,
    )
    print(f"SPARSE labelled replay  policy={policy.summary()}")
    print("max_candidate_gap disabled: M6 labels are tens of seconds apart.")
    status = 0
    for rec in manifest["recordings"]:
        samples = sorted(rec["samples"], key=lambda s: s["offset_s"])
        validator = TemporalScoreValidator(policy)
        visible = [(s["left_score"], s["right_score"]) for s in samples]
        for i, sample in enumerate(samples):
            observation = _pair_obs(
                sample["left_score"],
                sample["right_score"],
                index=i,
                ts=float(sample["offset_s"]),
                channel=manifest["channel_id"],
                profile_id=manifest["profile_id"],
            )
            validator.apply(observation)
        _summarize(f"{rec['key']} {rec['video_id']} {rec['matchup']}", validator, visible)
        confirmed = {c.new_pair for c in validator.history}
        if confirmed - set(visible):
            status = 1
    return status


def dense_replay() -> int:
    manifest = json.loads(DENSE_MANIFEST.read_text(encoding="utf-8"))
    channel = manifest["channel_id"]
    profile = load_profile(PROFILE, expect_channel=channel)
    reference = load_reference(REFERENCE, expect_channel=channel)
    glyphs = load_glyphs(GLYPHS, expect_channel=channel)
    pipeline = ScoreObservationPipeline(
        ScoreboardDetector(profile, reference, expect_channel=channel),
        ScoreReader(glyphs),
    )
    policy = ConfirmationPolicy()
    cadence = float(manifest["cadence_s"])
    print(f"DENSE 2s-cluster replay  policy={policy.summary()}")
    status = 0
    for rec in manifest["recordings"]:
        frames_dir = Path(rec["frames_dir"])
        validator = TemporalScoreValidator(policy)
        visible: list[tuple[int, int]] = []
        index = 0
        for cluster in rec["clusters"]:
            expected = (int(cluster["left_score"]), int(cluster["right_score"]))
            for step in range(3):
                offset = float(cluster["base_s"]) + step * cadence
                path = frames_dir / f"t{offset:08.3f}.png"
                image = cv2.imread(str(path))
                if image is None:
                    print(f"missing {path}\nRun: python tools/m7_fixture.py capture")
                    return 2
                observation = pipeline.observe(image)
                if observation is None:
                    validator.note_absence(
                        frame_index=index, timestamp_s=offset, source_epoch=1
                    )
                    index += 1
                    continue
                # Rebuild with sequential index + cluster timestamps so the
                # default live policy is exercised. Values come from M6 pixels.
                if not observation.both_readable:
                    print(f"  UNREADABLE {rec['key']} t={offset} L={observation.left.summary()} R={observation.right.summary()}")
                    status = 1
                    index += 1
                    continue
                got = (observation.left.value, observation.right.value)
                if got != expected:
                    print(f"  READ MISMATCH {rec['key']} t={offset} expected {expected} got {got}")
                    status = 1
                visible.append(got)
                stamped = RawScoreObservation(
                    frame_index=index,
                    timestamp_s=offset,
                    received_at=None,
                    left=observation.left,
                    right=observation.right,
                    detection_present=True,
                    detection_score=observation.detection_score,
                    detection_threshold=observation.detection_threshold,
                    channel_id=observation.channel_id,
                    profile_id=observation.profile_id,
                    profile_revision=observation.profile_revision,
                    frame_width=observation.frame_width,
                    frame_height=observation.frame_height,
                    source_epoch=1,
                )
                validator.apply(stamped)
                index += 1
        _summarize(f"{rec['key']} {rec['video_id']} {rec['matchup']}", validator, visible)
        confirmed = [c.new_pair for c in validator.history]
        expected_init = (
            rec["clusters"][0]["left_score"],
            rec["clusters"][0]["right_score"],
        )
        if not confirmed or confirmed[0] != expected_init:
            print(f"  expected init {expected_init}, got {confirmed}")
            status = 1
        if rec["clusters"][-1]["role"] == "after_change":
            expected_new = (
                rec["clusters"][-1]["left_score"],
                rec["clusters"][-1]["right_score"],
            )
            if expected_new not in confirmed:
                print(f"  expected confirmed change to {expected_new}, got {confirmed}")
                status = 1
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="M7 temporal-validator replay")
    parser.add_argument("--dense", action="store_true", help="real M6 reads of 2s clusters")
    args = parser.parse_args()
    status = sparse_replay()
    if args.dense:
        dense_status = dense_replay()
        status = status or dense_status
    return status


if __name__ == "__main__":
    raise SystemExit(main())
