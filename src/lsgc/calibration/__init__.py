"""Channel-bound scoreboard calibration.

Consumes frame imagery (`lsgc.models.Frame.image` or any equivalent BGR array)
and knows nothing about yt-dlp or FFmpeg, keeping the ingestion boundary
intact.

M2 scope: the calibration data model, its validation, serialization, and
developer tooling. No detection, matching, OCR, or recognition.
"""

from lsgc.calibration.errors import (
    CalibrationError,
    CalibrationValidationError,
    ChannelMismatchError,
    InvalidRegionError,
    ProfileSchemaError,
)
from lsgc.calibration.geometry import (
    ASPECT_RATIO_TOLERANCE,
    Region,
    same_aspect_ratio,
    scale_region,
)
from lsgc.calibration.overlay import render_overlay, save_overlay
from lsgc.calibration.profile import (
    SCHEMA_VERSION,
    CalibrationProfile,
    IdentityPresentation,
)
from lsgc.calibration.storage import (
    load_profile,
    load_profile_unchecked,
    profile_from_dict,
    profile_to_dict,
    save_profile,
)

__all__ = [
    "ASPECT_RATIO_TOLERANCE",
    "SCHEMA_VERSION",
    "CalibrationError",
    "CalibrationProfile",
    "CalibrationValidationError",
    "ChannelMismatchError",
    "IdentityPresentation",
    "InvalidRegionError",
    "ProfileSchemaError",
    "Region",
    "load_profile",
    "load_profile_unchecked",
    "profile_from_dict",
    "profile_to_dict",
    "render_overlay",
    "same_aspect_ratio",
    "save_overlay",
    "save_profile",
    "scale_region",
]
