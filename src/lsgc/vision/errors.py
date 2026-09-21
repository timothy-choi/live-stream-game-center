"""Vision error types.

Separate from `lsgc.errors` (ingestion) and `lsgc.calibration.errors`
(configuration) because a reference-evidence problem is neither: the media
arrived fine and the profile is valid, but the detector was handed evidence it
cannot use.
"""


class VisionError(Exception):
    """Base class for detection failures."""


class ReferenceSchemaError(VisionError):
    """Serialized reference evidence is malformed or an unsupported version."""


class ReferenceMismatchError(VisionError):
    """Reference evidence does not belong with the profile it was paired with.

    Raised when the channel, profile or anchor geometry disagree. Detecting
    with mismatched reference data would silently compare the wrong pixels.
    """
