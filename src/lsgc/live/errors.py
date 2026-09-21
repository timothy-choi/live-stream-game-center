"""Errors raised by a continuous live acquisition session.

These are ingestion problems, not vision or calibration problems. They sit
beside `SourceResolutionError` / `DecodeError` rather than replacing them:
those still describe a single resolve or decode attempt, while these describe
the session that coordinates many attempts.
"""

from lsgc.errors import IngestionError


class SessionError(IngestionError):
    """Base class for continuous-session failures."""


class SessionFailed(SessionError):
    """The session exhausted its retry policy and cannot continue."""


class SessionCancelled(SessionError):
    """The caller cancelled the session.

    `LiveFrameSession.frames()` stops cleanly rather than raising this; the
    type exists so tooling that wants an exception on cancel can wrap it.
    """
