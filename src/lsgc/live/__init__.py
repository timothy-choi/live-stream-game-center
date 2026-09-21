"""Continuous live-broadcast acquisition.

Coordinates resolver + decoder for one explicitly supplied YouTube watch URL.
Vision code consumes the resulting `Frame` objects and never imports this
package.
"""

from lsgc.live.errors import SessionCancelled, SessionError, SessionFailed
from lsgc.live.policy import RetryPolicy
from lsgc.live.sanitize import sanitize_text
from lsgc.live.session import LiveFrameSession
from lsgc.live.state import SessionHealth, SessionState

__all__ = [
    "LiveFrameSession",
    "RetryPolicy",
    "SessionCancelled",
    "SessionError",
    "SessionFailed",
    "SessionHealth",
    "SessionState",
    "sanitize_text",
]
