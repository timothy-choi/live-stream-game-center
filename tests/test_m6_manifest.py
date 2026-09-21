"""M6 fixture hygiene. Fully offline — no YouTube, no decoded frames."""

from __future__ import annotations

import json
from pathlib import Path

from lsgc.score import DEFAULT_MIN_MARGIN, DEFAULT_MIN_SCORE, GLYPH_HEIGHT, GLYPH_WIDTH
from lsgc.vision import DEFAULT_THRESHOLD

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "m6_score_validation_set.json"
GLYPHS = ROOT / "tests" / "fixtures" / "m6_digit_glyphs.json"
PROFILE = ROOT / "tests" / "fixtures" / "m1_fixture_profile.json"
REFERENCE = ROOT / "tests" / "fixtures" / "m3_reference.json"
CHANNEL = "UCz3q0f-dMPNIyQhntPTE7gQ"
CONSTRUCTION_VIDEO = "hBblAZD-ApY"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_exists_and_is_json() -> None:
    data = _manifest()
    assert data["channel_id"] == CHANNEL
    assert data["profile_id"] == "madden27-arcade-prismlive"
    assert data["glyphs"] == "tests/fixtures/m6_digit_glyphs.json"


def test_five_recordings_including_original_and_m5() -> None:
    data = _manifest()
    recs = data["recordings"]
    ids = [rec["video_id"] for rec in recs]
    assert ids[0] == CONSTRUCTION_VIDEO
    assert set(ids) == {
        "hBblAZD-ApY",
        "G2kMfIDFNIQ",
        "A8Qd1dKHKUE",
        "wDbkLLJT4i8",
        "UITXyHeI5KE",
    }
    assert len(set(ids)) == 5


def test_scores_are_non_negative_integers_on_present_crops() -> None:
    data = _manifest()
    for rec in data["recordings"]:
        assert rec["samples"], rec["key"]
        for sample in rec["samples"]:
            assert isinstance(sample["offset_s"], (int, float))
            assert isinstance(sample["left_score"], int)
            assert isinstance(sample["right_score"], int)
            assert sample["left_score"] >= 0
            assert sample["right_score"] >= 0
            assert sample["role"] in {"validation", "glyph_construction"}


def test_construction_frames_are_m3_only_and_cover_0_to_9() -> None:
    data = _manifest()
    construction = data["glyph_construction"]
    assert construction["video_id"] == CONSTRUCTION_VIDEO
    assert construction["digits_covered"] == list(range(10))
    offsets = set(construction["offsets"])
    giants = next(rec for rec in data["recordings"] if rec["video_id"] == CONSTRUCTION_VIDEO)
    tagged = {s["offset_s"] for s in giants["samples"] if s["role"] == "glyph_construction"}
    assert tagged == offsets
    for rec in data["recordings"]:
        if rec["video_id"] == CONSTRUCTION_VIDEO:
            continue
        assert all(s["role"] == "validation" for s in rec["samples"])


def test_held_out_is_not_mostly_one_score() -> None:
    data = _manifest()
    for rec in data["recordings"]:
        held = [s for s in rec["samples"] if s["role"] == "validation"]
        assert 20 <= len(held) <= 40, rec["key"]
        pairs = {(s["left_score"], s["right_score"]) for s in held}
        assert len(pairs) >= 8, rec["key"]


def test_offsets_documented_as_fixture_metadata() -> None:
    data = _manifest()
    assert "never an input" in data["offsets_are_fixture_metadata"]


def test_glyphs_file_has_ten_patches_and_no_score_table() -> None:
    glyphs = json.loads(GLYPHS.read_text(encoding="utf-8"))
    assert glyphs["channel_id"] == CHANNEL
    assert glyphs["glyph_width"] == GLYPH_WIDTH
    assert glyphs["glyph_height"] == GLYPH_HEIGHT
    assert set(glyphs["digits"]) == {str(d) for d in range(10)}
    for digit, patch in glyphs["digits"].items():
        assert len(patch) == GLYPH_HEIGHT
        assert len(patch[0]) == GLYPH_WIDTH
    blob = json.dumps(glyphs)
    assert "G2kMfIDFNIQ" not in blob
    assert "left_score" not in blob
    assert DEFAULT_MIN_SCORE == 0.80
    assert DEFAULT_MIN_MARGIN == 0.08
    assert DEFAULT_THRESHOLD == 0.55
    assert PROFILE.is_file()
    assert REFERENCE.is_file()
