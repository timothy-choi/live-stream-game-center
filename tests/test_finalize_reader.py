"""Postgame numeric reader follows configured regions, not hardcoded scores."""

from __future__ import annotations

from lsgc.finalize.reader import read_final_score
from tests.finalize_support import layout_a, layout_b, reader_for


def test_reads_configured_pair_from_pixels() -> None:
    image, profile, _reference, glyphs = layout_a(left=35, right=49)
    left, right = read_final_score(image, profile, reader_for(profile, glyphs))
    assert left.readable and right.readable
    assert left.value == 35
    assert right.value == 49


def test_region_change_moves_the_reader_without_code_changes() -> None:
    image, profile, _reference, glyphs = layout_a(left=12, right=8)
    swapped = profile.next_revision(
        left_final_score_region=profile.right_final_score_region,
        right_final_score_region=profile.left_final_score_region,
    )
    left, right = read_final_score(image, swapped, reader_for(swapped, glyphs))
    assert left.value == 8
    assert right.value == 12


def test_alternate_layout_uses_same_reader_function() -> None:
    image, profile, _reference, glyphs = layout_b(left=10, right=3)
    left, right = read_final_score(image, profile, reader_for(profile, glyphs))
    assert left.value == 10
    assert right.value == 3
