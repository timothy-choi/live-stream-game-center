"""Rectangles and resolution scaling for calibration geometry.

COORDINATE CONVENTION (used everywhere in calibration):

- Origin is the top-left pixel of the frame, (0, 0).
- x increases rightwards, y increases downwards.
- Units are whole pixels in the frame's own coordinate space. A region is only
  meaningful alongside the reference frame size it was measured against.
- A region covers the half-open box [x, x + width) x [y, y + height). So
  `right` and `bottom` are exclusive, and the region maps directly onto a
  numpy slice: `image[y : y + height, x : x + width]`.

The half-open choice is deliberate: it makes the slice mapping exact and makes
adjacent regions tile without overlapping or leaving a one-pixel gap. Note that
M1's exploratory notes quoted inclusive ranges like "357..932"; the same box is
written here as x=357, width=576.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from lsgc.calibration.errors import InvalidRegionError


def _require_int(name: str, value: object) -> int:
    # bool is an int subclass and is never a sensible coordinate.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRegionError(f"{name} must be an int, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class Region:
    """An axis-aligned rectangle in frame pixel coordinates.

    Invalid rectangles cannot be constructed, so anything holding a `Region`
    can assume it is well formed.
    """

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            _require_int(name, getattr(self, name))
        if self.x < 0 or self.y < 0:
            raise InvalidRegionError(
                f"Region origin must be non-negative, got x={self.x}, y={self.y}"
            )
        if self.width <= 0 or self.height <= 0:
            raise InvalidRegionError(
                f"Region must have positive size, got {self.width}x{self.height}"
            )

    @property
    def right(self) -> int:
        """Exclusive right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge."""
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains(self, other: Region) -> bool:
        """True when `other` lies entirely within this region (edges may touch)."""
        return (
            self.x <= other.x
            and self.y <= other.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def fits_within(self, width: int, height: int) -> bool:
        """True when this region lies inside a frame of the given size."""
        return self.x >= 0 and self.y >= 0 and self.right <= width and self.bottom <= height

    def as_slice(self) -> tuple[slice, slice]:
        """Row/column slices for indexing a HxW image array."""
        return slice(self.y, self.bottom), slice(self.x, self.right)

    def __str__(self) -> str:
        return f"x={self.x} y={self.y} {self.width}x{self.height}"


def _round_half_up(value: float) -> int:
    """Round halves away from zero for positive values.

    Python's built-in `round` uses banker's rounding, so round(0.5) == 0 and
    round(1.5) == 2. That is fine statistically but makes scaled geometry hard
    to predict and to assert on, so calibration rounds halves up consistently.
    """
    return math.floor(value + 0.5)


ASPECT_RATIO_TOLERANCE = 0.005
"""Relative aspect difference still considered the same shape.

Not zero, because real streaming ladders are not exactly proportional: YouTube
serves 480p as 854x480, which is 16:9 only to within 0.08%. Demanding exact
equality would refuse a resolution the pipeline will genuinely encounter, for a
sub-pixel error. A tolerance this tight still rejects the cases that matter -
4:3 is out by 33%, and mod-16 padded 1920x1088 by 0.74%.
"""


def same_aspect_ratio(
    from_size: tuple[int, int],
    to_size: tuple[int, int],
    *,
    tolerance: float = ASPECT_RATIO_TOLERANCE,
) -> bool:
    """True when two frame sizes describe the same shape within `tolerance`."""
    (from_w, from_h), (to_w, to_h) = from_size, to_size
    if min(from_w, from_h, to_w, to_h) <= 0:
        return False
    from_ratio = from_w / from_h
    return abs(from_ratio - to_w / to_h) <= tolerance * from_ratio


def scale_region(
    region: Region, *, from_size: tuple[int, int], to_size: tuple[int, int]
) -> Region:
    """Scale a region between two frame sizes of identical aspect ratio.

    Edges are scaled and rounded independently, then converted back to
    width/height. Scaling the edges rather than the origin-plus-size keeps
    regions that share a boundary in the reference frame sharing it after
    scaling, instead of drifting apart by a pixel.
    """
    from_w, from_h = from_size
    to_w, to_h = to_size
    scale_x, scale_y = to_w / from_w, to_h / from_h

    left = _round_half_up(region.x * scale_x)
    top = _round_half_up(region.y * scale_y)
    right = _round_half_up(region.right * scale_x)
    bottom = _round_half_up(region.bottom * scale_y)

    # Keep the result inside the target frame and never let rounding collapse a
    # valid region to zero pixels.
    right = min(right, to_w)
    bottom = min(bottom, to_h)
    left = max(0, min(left, right - 1))
    top = max(0, min(top, bottom - 1))

    return Region(x=left, y=top, width=right - left, height=bottom - top)
