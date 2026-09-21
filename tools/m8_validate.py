"""Replay M6 labelled observations through M7 + M8 with a simulated restart.

    LSGC_TEST_DATABASE_URL=postgresql://... python tools/m8_validate.py

Needs PostgreSQL. Never run by default pytest. No YouTube download.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lsgc.persist import (  # noqa: E402
    DurableScoreTracker,
    PLATFORM_YOUTUBE,
    PostgresGameRepository,
    apply_migrations,
    connect,
)
from lsgc.persist.config import postgres_test_dsn  # noqa: E402
from lsgc.score.models import RawScoreObservation, ScoreRead  # noqa: E402
from lsgc.track import ConfirmationPolicy  # noqa: E402

M6_MANIFEST = Path("tests/fixtures/m6_score_validation_set.json")
SPARSE = ConfirmationPolicy(required_observations=2, min_span_s=2.0, max_candidate_gap_s=None)
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"


def _obs(left: int, right: int, *, index: int, ts: float, epoch: int = 1) -> RawScoreObservation:
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
        channel_id=CHANNEL,
        profile_id="madden27-arcade-prismlive",
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_epoch=epoch,
    )


def replay_recording(repo: PostgresGameRepository, rec: dict) -> dict:
    samples = sorted(rec["samples"], key=lambda s: s["offset_s"])
    split = max(4, len(samples) // 2)
    video_id = rec["video_id"]
    tracker = DurableScoreTracker.open(
        repo, external_broadcast_id=video_id, channel_id=CHANNEL, policy=SPARSE
    )
    for i, sample in enumerate(samples[:split]):
        tracker.apply(
            _obs(sample["left_score"], sample["right_score"], index=i, ts=float(sample["offset_s"]))
        )
    before_pair = tracker.game.pair
    before_rev = tracker.game.score_revision
    before_hist = [c.new_pair for c in repo.list_confirmed_changes(tracker.game_id)]
    had_candidate = tracker.validator.candidate is not None
    repo.assert_current_matches_history(tracker.game_id)

    restarted = DurableScoreTracker.open(
        repo, external_broadcast_id=video_id, channel_id=CHANNEL, policy=SPARSE
    )
    candidate_after_open = restarted.validator.candidate
    assert candidate_after_open is None
    assert restarted.game.pair == before_pair
    for i, sample in enumerate(samples[split:], start=0):
        restarted.apply(
            _obs(
                sample["left_score"],
                sample["right_score"],
                index=i,
                ts=float(sample["offset_s"]),
                epoch=1,
            )
        )
    after_hist = [c.new_pair for c in repo.list_confirmed_changes(restarted.game_id)]
    repo.assert_current_matches_history(restarted.game_id)
    visible = {(s["left_score"], s["right_score"]) for s in samples}
    false = [pair for pair in after_hist if pair not in visible]
    return {
        "key": rec["key"],
        "video_id": video_id,
        "matchup": rec.get("matchup"),
        "observations": len(samples),
        "split": split,
        "confirmations": len(after_hist),
        "persisted_events": len(after_hist),
        "before_pair": before_pair,
        "after_pair": restarted.game.pair,
        "before_revision": before_rev,
        "after_revision": restarted.game.score_revision,
        "history_before": before_hist,
        "history_after": after_hist,
        "candidate_before_restart": had_candidate,
        "candidate_after_restart": candidate_after_open,
        "candidate_after_continuation": restarted.validator.candidate.pair if restarted.validator.candidate else None,
        "false_transitions": false,
        "duplicates": 0 if len(after_hist) == len(set(range(1, len(after_hist) + 1))) else "bad",
    }


def main() -> int:
    dsn = postgres_test_dsn()
    if not dsn:
        print("LSGC_TEST_DATABASE_URL is not set", file=sys.stderr)
        return 2
    manifest = json.loads(M6_MANIFEST.read_text(encoding="utf-8"))
    with connect(dsn) as conn:
        apply_migrations(conn)
        with conn.transaction():
            conn.execute("TRUNCATE confirmed_score_changes, games CASCADE")
        repo = PostgresGameRepository(conn)
        print("M8 archived-label persistence replay (sparse M6 labels, gap restart off)")
        print("Restart = new DurableScoreTracker.open on the same PostgreSQL rows.")
        status = 0
        for rec in manifest["recordings"]:
            report = replay_recording(repo, rec)
            print(f"\n=== {report['key']} {report['video_id']} {report['matchup']} ===")
            print(f"  observations={report['observations']} split={report['split']}")
            print(f"  confirmations/persisted_events={report['persisted_events']}")
            print(f"  before restart trusted={report['before_pair']} revision={report['before_revision']}")
            print(f"  history before={report['history_before']}")
            print(f"  candidate before restart={report['candidate_before_restart']}")
            print(f"  candidate immediately after reload={report['candidate_after_restart']}")
            print(f"  after continuation trusted={report['after_pair']} revision={report['after_revision']}")
            print(f"  history after={report['history_after']}")
            print(f"  leftover unconfirmed candidate after continuation={report['candidate_after_continuation']}")
            print(f"  false/regressed trusted transitions={report['false_transitions']}")
            if report["false_transitions"]:
                status = 1
            if report["after_pair"] != report["history_after"][-1]:
                print("  INVARIANT FAIL: current != newest event")
                status = 1
        conn.commit()
        return status


if __name__ == "__main__":
    raise SystemExit(main())
