"""The channel-bound calibration profile.

A profile records *where things are* for one channel's capture setup. It
deliberately records no opponent identity: which teams are playing is decided
per broadcast at runtime, never inherited from whatever game happened to be on
screen during calibration. It also records no source timestamps - the offsets
used to grab calibration frames are test fixture metadata, not calibration
input, because another broadcast will show its scoreboard at other times.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Flag, auto

from lsgc.calibration.errors import CalibrationValidationError, ChannelMismatchError
from lsgc.calibration.geometry import Region, same_aspect_ratio, scale_region

SCHEMA_VERSION = 1
"""Version of the profile *format*.

Distinct from `CalibrationProfile.revision`, which versions the *measurements*.
The schema version changes when this code changes the shape of the data; the
revision changes when a human recalibrates a channel. A channel recalibrated
ten times still has schema version 1.
"""


class IdentityPresentation(Flag):
    """What usable identity information an identity region exposes.

    A flag rather than an enum because a layout may show several at once, and
    deliberately not a team enum: this describes the *presentation*, never
    which team is playing.
    """

    NAME = auto()
    ABBREVIATION = auto()
    LOGO = auto()

    @classmethod
    def parse(cls, names: str | list[str]) -> IdentityPresentation:
        """Build from names, e.g. "logo" or ["name", "logo"]."""
        if isinstance(names, str):
            names = [part for part in names.replace(",", " ").split() if part]
        if not names:
            raise CalibrationValidationError("Identity presentation cannot be empty.")
        result = cls(0)
        for name in names:
            try:
                result |= cls[name.strip().upper()]
            except KeyError:
                valid = ", ".join(member.name.lower() for member in cls)
                raise CalibrationValidationError(
                    f"Unknown identity presentation {name!r}. Expected any of: {valid}."
                ) from None
        return result

    def to_names(self) -> list[str]:
        """Sorted lowercase member names, for stable serialization."""
        return sorted(member.name.lower() for member in type(self) if member & self)


@dataclass(frozen=True)
class CalibrationProfile:
    """Calibrated scoreboard geometry for one channel's capture setup.

    Frozen: recalibration produces a new profile via `next_revision` rather
    than mutating an existing one, so an older revision keeps its meaning.
    """

    profile_id: str
    """Identifies the calibration lineage for a channel. Stable across revisions."""

    channel_id: str
    """Stable platform channel id (e.g. a YouTube UC... id), never a display name."""

    revision: int
    """Increments each time a human recalibrates. Starts at 1."""

    reference_width: int
    reference_height: int
    """Frame size the regions below were measured against."""

    scoreboard: Region
    left_score: Region
    right_score: Region
    left_identity: Region
    right_identity: Region

    left_identity_presentation: IdentityPresentation
    right_identity_presentation: IdentityPresentation

    stable_anchors: tuple[Region, ...]
    """Small structural regions expected to stay put while the real scoreboard
    is on screen. These exist to tell "the calibrated scoreboard is present"
    apart from "something occupies the scoreboard band" - M1 found a TOUCHDOWN
    banner sitting exactly where the scoreboard goes. Matching is a later
    milestone; a profile only records where to look."""

    dynamic_ignore: tuple[Region, ...] = ()
    """Regions expected to change while the scoreboard is displayed, so they
    must not be used as stable evidence that it is present. Generic rectangles:
    a profile does not label which is the clock and which is the down marker.
    Score and identity regions stay separately represented even when they also
    appear here."""

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: str = ""
    """Free text, typically why a recalibration happened."""

    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()

    # -- validation --------------------------------------------------------

    def validate(self) -> None:
        """Raise `CalibrationValidationError` describing every problem found."""
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

        if problems:
            # Geometry checks below assume a sane reference frame.
            raise CalibrationValidationError("Invalid calibration profile: " + "; ".join(problems))

        frame = f"{self.reference_width}x{self.reference_height} reference frame"
        if not self.scoreboard.fits_within(self.reference_width, self.reference_height):
            problems.append(f"scoreboard {self.scoreboard} lies outside the {frame}")

        # Score and identity regions belong to the scoreboard, so they must sit
        # inside it. Checked against the scoreboard rather than the frame
        # because a region inside the frame but outside the board is a
        # mis-calibration we can catch for free.
        for name in ("left_score", "right_score", "left_identity", "right_identity"):
            region: Region = getattr(self, name)
            if not region.fits_within(self.reference_width, self.reference_height):
                problems.append(f"{name} {region} lies outside the {frame}")
            elif not self.scoreboard.contains(region):
                problems.append(
                    f"{name} {region} is not inside scoreboard bounds {self.scoreboard}"
                )

        for side in ("left", "right"):
            presentation = getattr(self, f"{side}_identity_presentation")
            if not isinstance(presentation, IdentityPresentation) or not presentation:
                problems.append(
                    f"{side}_identity_presentation must name at least one of "
                    "name/abbreviation/logo"
                )

        if not self.stable_anchors:
            problems.append(
                "at least one stable anchor is required; without one there is no way to "
                "tell the calibrated scoreboard from another graphic in the same place"
            )
        for index, anchor in enumerate(self.stable_anchors):
            if not self.scoreboard.contains(anchor):
                problems.append(
                    f"stable_anchors[{index}] {anchor} is not inside scoreboard bounds "
                    f"{self.scoreboard}"
                )

        # Dynamic regions are only required to be on screen. Something outside
        # the board can still be a distraction worth ignoring, and forbidding
        # that would encode an assumption this one sample does not justify.
        for index, ignored in enumerate(self.dynamic_ignore):
            if not ignored.fits_within(self.reference_width, self.reference_height):
                problems.append(f"dynamic_ignore[{index}] {ignored} lies outside the {frame}")

        if self.created_at.tzinfo is None:
            problems.append("created_at must be timezone-aware")

        if problems:
            raise CalibrationValidationError("Invalid calibration profile: " + "; ".join(problems))

    # -- channel binding ---------------------------------------------------

    def matches_channel(self, channel_id: str) -> bool:
        return self.channel_id == channel_id

    def assert_channel(self, channel_id: str) -> None:
        """Guard before using a profile against a broadcast.

        Calibration describes one channel's capture setup. Applying it to
        another channel would silently produce wrong geometry, so this raises
        rather than returning a boolean that a caller can forget to check.
        """
        if not self.matches_channel(channel_id):
            raise ChannelMismatchError(
                f"Profile {self.profile_id!r} is calibrated for channel "
                f"{self.channel_id!r}, not {channel_id!r}. Calibration is per-channel; "
                "calibrate this channel instead of reusing another channel's profile."
            )

    # -- derived profiles --------------------------------------------------

    def for_frame_size(self, width: int, height: int) -> CalibrationProfile:
        """Return this profile expressed against a different frame size.

        Only same-aspect-ratio rescaling. Anything else is rejected rather than
        silently distorted, because a letterboxed or cropped feed needs real
        registration work, not a stretch.
        """
        if width <= 0 or height <= 0:
            raise CalibrationValidationError(
                f"Target frame must be positive, got {width}x{height}"
            )
        source = (self.reference_width, self.reference_height)
        target = (width, height)
        if not same_aspect_ratio(source, target):
            raise CalibrationValidationError(
                f"Cannot rescale calibration from {self.reference_width}x"
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
            scoreboard=rescale(self.scoreboard),
            left_score=rescale(self.left_score),
            right_score=rescale(self.right_score),
            left_identity=rescale(self.left_identity),
            right_identity=rescale(self.right_identity),
            stable_anchors=tuple(rescale(r) for r in self.stable_anchors),
            dynamic_ignore=tuple(rescale(r) for r in self.dynamic_ignore),
        )

    def next_revision(self, **changes: object) -> CalibrationProfile:
        """Produce the next revision of this calibration.

        The existing instance is untouched, so an earlier revision keeps
        meaning exactly what it meant when it was written.
        """
        changes.setdefault("revision", self.revision + 1)
        changes.setdefault("created_at", datetime.now(UTC))
        return replace(self, **changes)  # type: ignore[arg-type]

    def summary(self) -> str:
        """Readable summary for developer tooling."""
        lines = [
            f"profile_id   {self.profile_id}",
            f"channel_id   {self.channel_id}",
            f"revision     {self.revision}   (schema version {self.schema_version})",
            f"reference    {self.reference_width}x{self.reference_height}",
            f"created_at   {self.created_at.isoformat()}",
            f"scoreboard   {self.scoreboard}  area={self.scoreboard.area}px",
            f"left score   {self.left_score}",
            f"right score  {self.right_score}",
            f"left ident   {self.left_identity}  "
            f"[{', '.join(self.left_identity_presentation.to_names())}]",
            f"right ident  {self.right_identity}  "
            f"[{', '.join(self.right_identity_presentation.to_names())}]",
            f"anchors      {len(self.stable_anchors)}",
        ]
        lines += [f"  anchor[{i}]  {r}" for i, r in enumerate(self.stable_anchors)]
        lines.append(f"dynamic      {len(self.dynamic_ignore)}")
        lines += [f"  ignore[{i}]  {r}" for i, r in enumerate(self.dynamic_ignore)]
        if self.notes:
            lines.append(f"notes        {self.notes}")
        return "\n".join(lines)
