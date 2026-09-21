"""M7 dense-replay fixture hygiene. Fully offline."""

from __future__ import annotations

import json
from pathlib import Path

from lsgc.track import ConfirmationPolicy

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "m7_dense_replay.json"
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"


def test_dense_manifest_matches_default_policy() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    policy = ConfirmationPolicy()
    stated = data["policy_under_test"]
    assert stated["required_observations"] == policy.required_observations
    assert stated["min_span_s"] == policy.min_span_s
    assert stated["max_candidate_gap_s"] == policy.max_candidate_gap_s
    assert data["cadence_s"] == 2.0
    assert data["channel_id"] == CHANNEL


def test_five_recordings_and_initialize_plus_change() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recs = data["recordings"]
    assert [r["video_id"] for r in recs] == [
        "hBblAZD-ApY",
        "G2kMfIDFNIQ",
        "A8Qd1dKHKUE",
        "wDbkLLJT4i8",
        "UITXyHeI5KE",
    ]
    for rec in recs:
        roles = [c["role"] for c in rec["clusters"]]
        assert roles[0] == "initialize"
        assert "before_change" in roles and "after_change" in roles
        for cluster in rec["clusters"]:
            assert isinstance(cluster["left_score"], int)
            assert isinstance(cluster["right_score"], int)
            assert cluster["left_score"] >= 0
            assert cluster["right_score"] >= 0


def test_track_runtime_has_no_video_score_table() -> None:
    root = ROOT / "src" / "lsgc" / "track"
    forbidden = ("hBblAZD-ApY", "G2kMfIDFNIQ", "A8Qd1dKHKUE", "wDbkLLJT4i8", "UITXyHeI5KE")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text
        assert "m7_dense_replay" not in text
        assert "m6_score_validation_set" not in text
