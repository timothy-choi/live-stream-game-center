"""Profile-driven completion detector: positives, negatives, generality."""

from __future__ import annotations

from pathlib import Path

import pytest

from lsgc.calibration.geometry import Region
from lsgc.finalize.detector import CompletionDetector, CompletionPresence
from lsgc.vision.reference import build_reference
from tests.finalize_support import (
    gameplay_imposter,
    layout_a,
    layout_b,
    menu_frame,
    scale_image,
)

PROBE_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "finalization"


def test_layout_a_present_and_gameplay_absent() -> None:
    image, profile, reference, _ = layout_a()
    detector = CompletionDetector(profile, reference)
    assert detector.presence(image) is CompletionPresence.POSTGAME_PRESENT
    assert detector.detect(image).present
    assert not detector.detect(gameplay_imposter(profile)).present
    assert not detector.detect(menu_frame(profile)).present


def test_single_anchor_is_insufficient_to_construct() -> None:
    image, profile, reference, _ = layout_a()
    # The profile already requires >= 2 anchors. Changing the reference pairing
    # to disagree is a mismatch, not a successful one-anchor detect.
    with pytest.raises(Exception):
        CompletionDetector(
            profile,
            build_reference([image], profile.next_revision(stable_anchors=profile.stable_anchors[:1])),
        )


def test_wrong_profile_does_not_match() -> None:
    image_a, profile_a, reference_a, _ = layout_a()
    image_b, profile_b, reference_b, _ = layout_b()
    detect_a = CompletionDetector(profile_a, reference_a)
    detect_b = CompletionDetector(profile_b, reference_b)
    assert detect_a.detect(image_a).present
    assert detect_b.detect(image_b).present
    assert not detect_a.detect(image_b).present
    assert not detect_b.detect(image_a).present


def test_same_runtime_two_layouts() -> None:
    image_a, profile_a, reference_a, _ = layout_a()
    image_b, profile_b, reference_b, _ = layout_b()
    assert type(CompletionDetector(profile_a, reference_a)) is type(
        CompletionDetector(profile_b, reference_b)
    )
    assert CompletionDetector(profile_a, reference_a).detect(image_a).present
    assert CompletionDetector(profile_b, reference_b).detect(image_b).present


def test_anchor_change_follows_profile_without_code_changes() -> None:
    image, profile, reference, _ = layout_a()
    original = CompletionDetector(profile, reference)
    assert original.detect(image).present
    empty = profile.next_revision(
        stable_anchors=(
            Region(620, 200, 40, 40),
            Region(680, 200, 40, 40),
            Region(620, 250, 40, 40),
            Region(680, 250, 40, 40),
        )
    )
    empty_reference = build_reference([image], empty)
    moved = CompletionDetector(empty, empty_reference)
    assert type(original) is type(moved)
    assert not moved.detect(image).present


def test_dynamic_score_pixels_are_not_required_structure() -> None:
    image_a, profile, reference, _ = layout_a(left=7, right=0)
    image_b, _, _, _ = layout_a(left=28, right=21)
    detector = CompletionDetector(profile, reference)
    assert detector.detect(image_a).present
    assert detector.detect(image_b).present


def test_rescaled_geometry() -> None:
    image, profile, reference, _ = layout_a()
    scaled = scale_image(image, 1920, 1080)
    detector = CompletionDetector(profile, reference)
    assert detector.detect(scaled).present


@pytest.mark.skipif(
    not any(PROBE_DIR.glob("probe_t*.png")),
    reason="archived gameplay frames are gitignored",
)
def test_archived_gameplay_frames_are_absent() -> None:
    import cv2

    _image, profile, reference, _ = layout_a()
    detector = CompletionDetector(profile, reference)
    for path in sorted(PROBE_DIR.glob("probe_t*.png")):
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert frame is not None
        assert not detector.detect(frame).present, path.name
