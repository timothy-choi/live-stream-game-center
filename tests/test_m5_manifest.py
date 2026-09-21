"""M5 fixture hygiene. Fully offline — no YouTube, no decoded frames."""

from __future__ import annotations

import json
from pathlib import Path

from lsgc.vision import DEFAULT_THRESHOLD

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "m5_validation_set.json"
PROFILE = ROOT / "tests" / "fixtures" / "m1_fixture_profile.json"
REFERENCE = ROOT / "tests" / "fixtures" / "m3_reference.json"
ORIGINAL_FIXTURE = "hBblAZD-ApY"
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"


def test_m5_manifest_exists_and_is_json() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["channel_id"] == CHANNEL
    assert data["profile_id"] == "madden27-arcade-prismlive"
    assert data["reference_source_video_id"] == ORIGINAL_FIXTURE


def test_original_m3_fixture_is_excluded() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ids = {rec["video_id"] for rec in data["recordings"]}
    assert ORIGINAL_FIXTURE not in ids
    assert "PWw6gEH32bo" not in ids
    assert "nI4DUFbEvrQ" not in ids


def test_four_distinct_same_channel_recordings() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recs = data["recordings"]
    assert len(recs) == 4
    ids = [rec["video_id"] for rec in recs]
    assert len(set(ids)) == 4
    keys = [rec["key"] for rec in recs]
    assert len(set(keys)) == 4
    for rec in recs:
        assert rec["source_url"].endswith(rec["video_id"])
        assert rec["source_url"].startswith("https://www.youtube.com/watch?v=")


def test_samples_are_labelled_fixture_metadata() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    total = 0
    for rec in data["recordings"]:
        samples = rec["samples"]
        assert 20 <= len(samples) <= 40
        present = 0
        for sample in samples:
            assert isinstance(sample["offset_s"], (int, float))
            assert isinstance(sample["scoreboard_present"], bool)
            assert sample["state"]
            if sample["scoreboard_present"]:
                present += 1
            total += 1
        assert present >= 1
        assert len(samples) - present >= 1
    assert total >= 80


def test_baseline_paths_and_threshold_unchanged() -> None:
    assert PROFILE.is_file()
    assert REFERENCE.is_file()
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert profile["channel_id"] == CHANNEL
    assert reference["channel_id"] == CHANNEL
    assert profile["stable_anchors"][0] == {"x": 529, "y": 70, "width": 16, "height": 56}
    assert profile["stable_anchors"][1] == {"x": 734, "y": 72, "width": 18, "height": 52}
    assert DEFAULT_THRESHOLD == 0.55
