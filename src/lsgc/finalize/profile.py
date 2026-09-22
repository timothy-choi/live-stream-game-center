"""Channel-bound final-screen calibration.

A FinalizationProfile records where a completed-game screen is structurally
stable for one channel's capture setup. It is not a video-game ruleset and it
does not identify teams. Recalibration produces a new revision; runtime code
reads the saved data rather than assuming a particular postgame layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from lsgc.calibration.errors import ChannelMismatchError
from lsgc.calibration.geometry import Region, same_aspect_ratio, scale_region
from lsgc.finalize.errors import FinalizationValidationError
from lsgc.vision.detector import DEFAULT_THRESHOLD
from lsgc.score.reader import DEFAULT_MIN_MARGIN, DEFAULT_MIN_SCORE

SCHEMA_VERSION = 1
"""Version of the finalization profile *format*.

Distinct from `FinalizationProfile.revision`, which versions the measurements.
"""

MIN_STABLE_ANCHORS = 2
"""A single matching region is not enough to claim the completed-game screen."""


@dataclass(frozen=True)
class FinalizationProfile:
    """Calibrated completed-game geometry for one channel/profile lineage."""

    profile_id: str
    channel_id: str
    revision: int
    reference_width: int
    reference_height: int
    completion_panel: Region
    """Bounding panel that contains anchors and final-score regions.

    This is geometry for THIS profile, not a product-wide completed-game template.
    """

    stable_anchors: tuple[Region, ...]
    left_final_score_region: Region
    right_final_score_region: Region
    dynamic_ignore: tuple[Region, ...] = ()
    presence_threshold: float = DEFAULT_THRESHOLD
    reader_min_score: float = DEFAULT_MIN_SCORE
    reader_min_margin: float = DEFAULT_MIN_MARGIN
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        problems: list[str] = []
        if self.schema_version != SCHEMA_VERSION:
            problems.append(
                f"schema_version {self.schema_version} is not supported "
                f"(this build understands {SCHEMA_VERSION})"
            )
        if not self.profile_id.strip():
            problems.append("profile_id must not be empty")
        if not self.channel_id.strip():
            problems.append("channel_id must not be empty")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            problems.append("revision must be an int")
        elif self.revision < 1:
            problems.append(f"revision must be >= 1, got {self.revision}")
        if self.reference_width <= 0 or self.reference_height <= 0:
            problems.append(
                "reference frame must be positive, got "
                f"{self.reference_width}x{self.reference_height}"
            )
        if not -1.0 <= self.presence_threshold <= 1.0:
            problems.append("presence_threshold must lie in [-1, 1]")
        if not -1.0 <= self.reader_min_score <= 1.0:
            problems.append("reader_min_score must lie in [-1, 1]")
        if self.reader_min_margin < 0:
            problems.append("reader_min_margin must be >= 0")
        if problems:
            raise FinalizationValidationError(
                "Invalid finalization profile: " + "; ".join(problems)
            )

        frame = f"{self.reference_width}x{self.reference_height} reference frame"
        if not self.completion_panel.fits_within(self.reference_width, self.reference_height):
            problems.append(f"completion_panel {self.completion_panel} lies outside the {frame}")

        if len(self.stable_anchors) < MIN_STABLE_ANCHORS:
            problems.append(
                f"at least {MIN_STABLE_ANCHORS} stable anchors are required; "
                "a single matching region is not completion evidence"
            )
        for index, anchor in enumerate(self.stable_anchors):
            if not self.completion_panel.contains(anchor):
                problems.append(
                    f"stable_anchors[{index}] {anchor} is not inside completion_panel "
                    f"{self.completion_panel}"
                )

        for name in ("left_final_score_region", "right_final_score_region"):
            region: Region = getattr(self, name)
            if not region.fits_within(self.reference_width, self.reference_height):
                problems.append(f"{name} {region} lies outside the {frame}")
            elif not self.completion_panel.contains(region):
                problems.append(
                    f"{name} {region} is not inside completion_panel {self.completion_panel}"
                )
            for index, anchor in enumerate(self.stable_anchors):
                if region == anchor:
                    problems.append(
                        f"{name} must not equal stable_anchors[{index}]; "
                        "score digits are dynamic and cannot be structural anchors"
                    )

        for index, ignored in enumerate(self.dynamic_ignore):
            if not ignored.fits_within(self.reference_width, self.reference_height):
                problems.append(
                    f"dynamic_ignore[{index}] {ignored} lies outside the {frame}"
                )

        if self.created_at.tzinfo is None:
            problems.append("created_at must be timezone-aware")

        if problems:
            raise FinalizationValidationError(
                "Invalid finalization profile: " + "; ".join(problems)
            )

    def matches_channel(self, channel_id: str) -> bool:
        return self.channel_id == channel_id

    def assert_channel(self, channel_id: str) -> None:
        if not self.matches_channel(channel_id):
            raise ChannelMismatchError(
                f"Finalization profile {self.profile_id!r} is calibrated for channel "
                f"{self.channel_id!r}, not {channel_id!r}."
            )

    def for_frame_size(self, width: int, height: int) -> FinalizationProfile:
        if width <= 0 or height <= 0:
            raise FinalizationValidationError(
                f"Target frame must be positive, got {width}x{height}"
            )
        source = (self.reference_width, self.reference_height)
        target = (width, height)
        if not same_aspect_ratio(source, target):
            raise FinalizationValidationError(
                f"Cannot rescale finalization from {self.reference_width}x"
                f"{self.reference_height} to {width}x{height}: different aspect ratios. "
                "Recalibrate for this frame shape."
            )
        if source == target:
            return self

        def rescale(region: Region) -> Region:
            return scale_region(region, from_size=source, to_size=target)

        return replace(
            self,
            reference_width=width,
            reference_height=height,
            completion_panel=rescale(self.completion_panel),
            left_final_score_region=rescale(self.left_final_score_region),
            right_final_score_region=rescale(self.right_final_score_region),
            stable_anchors=tuple(rescale(r) for r in self.stable_anchors),
            dynamic_ignore=tuple(rescale(r) for r in self.dynamic_ignore),
        )

    def next_revision(self, **changes: object) -> FinalizationProfile:
        changes.setdefault("revision", self.revision + 1)
        changes.setdefault("created_at", datetime.now(UTC))
        return replace(self, **changes)  # type: ignore[arg-type]

    def summary(self) -> str:
        lines = [
            f"profile_id   {self.profile_id}",
            f"channel_id   {self.channel_id}",
            f"revision     {self.revision}   (schema version {self.schema_version})",
            f"reference    {self.reference_width}x{self.reference_height}",
            f"created_at   {self.created_at.isoformat()}",
            f"panel        {self.completion_panel}",
            f"left score   {self.left_final_score_region}",
            f"right score  {self.right_final_score_region}",
            f"threshold    {self.presence_threshold}",
            f"anchors      {len(self.stable_anchors)}",
        ]
        lines += [f"  anchor[{i}]  {r}" for i, r in enumerate(self.stable_anchors)]
        lines.append(f"dynamic      {len(self.dynamic_ignore)}")
        lines += [f"  ignore[{i}]  {r}" for i, r in enumerate(self.dynamic_ignore)]
        if self.notes:
            lines.append(f"notes        {self.notes}")
        return "\n".join(lines)
