"""Region semantics and resolution scaling. Fully offline."""

from __future__ import annotations

import pytest

from lsgc.calibration import InvalidRegionError, Region, same_aspect_ratio, scale_region
from lsgc.calibration.geometry import _round_half_up


class TestRegion:
    def test_valid_region_exposes_edges_and_area(self) -> None:
        region = Region(x=357, y=65, width=576, height=65)
        assert (region.right, region.bottom) == (933, 130)
        assert region.area == 576 * 65

    def test_edges_are_exclusive_and_match_numpy_slicing(self) -> None:
        """The half-open convention must line up with image[y:y+h, x:x+w]."""
        import numpy as np

        image = np.zeros((100, 100), dtype=np.uint8)
        region = Region(10, 20, 30, 40)
        image[region.as_slice()] = 1
        assert image.sum() == region.area
        assert image[20, 10] == 1
        assert image[region.bottom, region.right] == 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"x": -1, "y": 0, "width": 10, "height": 10},
            {"x": 0, "y": -5, "width": 10, "height": 10},
            {"x": 0, "y": 0, "width": 0, "height": 10},
            {"x": 0, "y": 0, "width": 10, "height": 0},
            {"x": 0, "y": 0, "width": -3, "height": 10},
        ],
    )
    def test_invalid_geometry_is_unconstructible(self, kwargs: dict) -> None:
        with pytest.raises(InvalidRegionError):
            Region(**kwargs)

    @pytest.mark.parametrize("bad", [1.5, "10", True, None])
    def test_non_integer_coordinates_rejected(self, bad: object) -> None:
        """Floats would drift under scaling; bool is an int subclass by accident."""
        with pytest.raises(InvalidRegionError):
            Region(x=bad, y=0, width=10, height=10)  # type: ignore[arg-type]

    def test_contains_including_touching_edges(self) -> None:
        outer = Region(100, 100, 200, 200)
        assert outer.contains(Region(100, 100, 200, 200))  # identical
        assert outer.contains(Region(150, 150, 10, 10))
        assert not outer.contains(Region(99, 100, 10, 10))
        assert not outer.contains(Region(290, 100, 20, 10))  # crosses right edge

    def test_fits_within_frame(self) -> None:
        assert Region(0, 0, 1280, 720).fits_within(1280, 720)
        assert not Region(1, 0, 1280, 720).fits_within(1280, 720)


class TestRoundingRule:
    @pytest.mark.parametrize(
        ("value", "expected"), [(0.5, 1), (1.5, 2), (2.5, 3), (1.4, 1), (1.6, 2), (3.0, 3)]
    )
    def test_halves_round_up(self, value: float, expected: int) -> None:
        assert _round_half_up(value) == expected

    def test_differs_from_builtin_bankers_rounding(self) -> None:
        """Documents why the helper exists: round(0.5) is 0, round(2.5) is 2."""
        assert _round_half_up(0.5) != round(0.5)
        assert _round_half_up(2.5) != round(2.5)


class TestAspectRatio:
    @pytest.mark.parametrize("size", [(1920, 1080), (640, 360), (1280, 720), (3840, 2160)])
    def test_accepts_proportional_sizes(self, size: tuple[int, int]) -> None:
        assert same_aspect_ratio((1280, 720), size)

    def test_accepts_youtubes_inexact_480p_rung(self) -> None:
        """854x480 is 16:9 to within 0.08%; refusing it would block a real rung."""
        assert same_aspect_ratio((1280, 720), (854, 480))

    @pytest.mark.parametrize(
        "size",
        [
            (1280, 1024),  # 5:4
            (1440, 1080),  # 4:3
            (1920, 1088),  # mod-16 padded, 0.74% out
            (2560, 1080),  # ultrawide
        ],
    )
    def test_rejects_different_shapes(self, size: tuple[int, int]) -> None:
        assert not same_aspect_ratio((1280, 720), size)

    def test_rejects_degenerate_sizes(self) -> None:
        assert not same_aspect_ratio((1280, 720), (0, 0))
        assert not same_aspect_ratio((1280, 720), (-1920, 1080))

    def test_is_symmetric_for_the_cases_we_rely_on(self) -> None:
        assert same_aspect_ratio((1920, 1080), (1280, 720))
        assert not same_aspect_ratio((1280, 1024), (1280, 720))


class TestScaleRegion:
    def test_720p_to_1080p_is_deterministic(self) -> None:
        scaled = scale_region(
            Region(357, 65, 576, 65), from_size=(1280, 720), to_size=(1920, 1080)
        )
        # edges scaled by 1.5 then rounded half-up: 535.5->536, 97.5->98,
        # 1399.5->1400, 195.0->195
        assert scaled == Region(536, 98, 864, 97)

    def test_scaling_is_repeatable(self) -> None:
        region = Region(470, 89, 59, 26)
        args = {"from_size": (1280, 720), "to_size": (1920, 1080)}
        assert scale_region(region, **args) == scale_region(region, **args)

    def test_result_stays_inside_target_frame(self) -> None:
        scaled = scale_region(
            Region(0, 0, 1280, 720), from_size=(1280, 720), to_size=(1920, 1080)
        )
        assert scaled.fits_within(1920, 1080)
        assert scaled == Region(0, 0, 1920, 1080)

    def test_downscaling_never_collapses_a_region(self) -> None:
        """A 1px region scaled down must stay at least 1px, not vanish."""
        for x in range(90, 110):
            scaled = scale_region(
                Region(x, x, 1, 1), from_size=(1920, 1080), to_size=(640, 360)
            )
            assert scaled.width >= 1 and scaled.height >= 1
            assert scaled.fits_within(640, 360)

    def test_adjacent_regions_stay_adjacent(self) -> None:
        """Scaling edges rather than origin+size keeps shared boundaries shared."""
        left = Region(100, 10, 51, 20)
        right = Region(151, 10, 49, 20)
        args = {"from_size": (1280, 720), "to_size": (1920, 1080)}
        assert scale_region(left, **args).right == scale_region(right, **args).x
