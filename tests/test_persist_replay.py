"""Archived M6-label replay through M7+M8 with a real process-restart.

Requires LSGC_TEST_DATABASE_URL. No YouTube access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lsgc.persist import (
    DurableScoreTracker,
    PLATFORM_YOUTUBE,
    PostgresGameRepository,
    apply_migrations,
    connect,
)
from lsgc.persist.config import postgres_test_dsn
from lsgc.score.models import RawScoreObservation
from lsgc.track import ConfirmationPolicy
from tests.test_track_validator import obs

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parent.parent
M6_MANIFEST = ROOT / "tests" / "fixtures" / "m6_score_validation_set.json"
SPARSE = ConfirmationPolicy(required_observations=2, min_span_s=2.0, max_candidate_gap_s=None)
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"


@pytest.fixture(scope="module")
def pg_repo():
    dsn = postgres_test_dsn()
    if not dsn:
        pytest.skip("LSGC_TEST_DATABASE_URL is not set")
    conn = connect(dsn)
    apply_migrations(conn)
    conn.commit()
    with conn.transaction():
        conn.execute("TRUNCATE confirmed_score_changes, games CASCADE")
    yield PostgresGameRepository(conn)
    conn.close()


def _obs_from_sample(sample: dict, index: int, epoch: int = 1) -> RawScoreObservation:
    return obs(
        int(sample["left_score"]),
        int(sample["right_score"]),
        index=index,
        ts=float(sample["offset_s"]),
        epoch=epoch,
    )


def test_five_recordings_persist_restart_and_continue(pg_repo: PostgresGameRepository) -> None:
    manifest = json.loads(M6_MANIFEST.read_text(encoding="utf-8"))
    keys = ["giants", "chargers", "packers", "raiders", "vikings"]
    for rec in manifest["recordings"]:
        if rec["key"] not in keys:
            continue
        samples = sorted(rec["samples"], key=lambda s: s["offset_s"])
        split = max(4, len(samples) // 2)
        video_id = rec["video_id"]
        tracker = DurableScoreTracker.open(
            pg_repo,
            external_broadcast_id=video_id,
            channel_id=CHANNEL,
            policy=SPARSE,
        )
        for index, sample in enumerate(samples[:split]):
            tracker.apply(_obs_from_sample(sample, index=index, epoch=1))
        before_pair = tracker.game.pair
        before_rev = tracker.game.score_revision
        before_hist = len(pg_repo.list_confirmed_changes(tracker.game_id))
        pg_repo.assert_current_matches_history(tracker.game_id)

        restarted = DurableScoreTracker.open(
            pg_repo,
            external_broadcast_id=video_id,
            channel_id=CHANNEL,
            policy=SPARSE,
        )
        assert restarted.game.pair == before_pair
        assert restarted.game.score_revision == before_rev
        assert len(restarted.validator.history) == before_hist
        assert restarted.validator.candidate is None
        # New process Frame.index origin starts at 0.
        for index, sample in enumerate(samples[split:]):
            restarted.apply(_obs_from_sample(sample, index=index, epoch=1))
        pg_repo.assert_current_matches_history(restarted.game_id)
        after_hist = len(pg_repo.list_confirmed_changes(restarted.game_id))
        assert after_hist >= before_hist
        assert restarted.game.score_revision == after_hist

    found = 0
    for rec in manifest["recordings"]:
        if rec["key"] not in keys:
            continue
        found += 1
        game = pg_repo.get_game_by_external_id(PLATFORM_YOUTUBE, rec["video_id"])
        assert game is not None
        revs = [c.revision for c in pg_repo.list_confirmed_changes(game.id)]
        assert revs == list(range(1, len(revs) + 1))
    assert found == 5
