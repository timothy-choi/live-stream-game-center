"""Data passed between ingestion layers.

`MediaSource` is the contract between source resolution and decoding.
`Frame` is the contract between decoding and the future vision pipeline.
Neither mentions YouTube, yt-dlp, or ffmpeg, so the vision code that consumes
`Frame` never has to change when the resolver does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MediaSource:
    """Playable media resolved from a user-facing source URL."""

    source_url: str
    """The original URL requested (e.g. the YouTube watch page)."""

    media_url: str
    """A direct, decoder-readable media URL (progressive, DASH, or HLS)."""

    is_live: bool
    source_id: str | None = None
    title: str | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    format_id: str | None = None
    protocol: str | None = None
    extractor: str | None = None

    http_headers: dict[str, str] = field(default_factory=dict)
    """Headers the decoder must replay; CDN URLs are often user-agent bound."""

    fragment_urls: tuple[str, ...] = ()
    """Ordered media segments, when the format is delivered as DASH segments.

    Past-livestream VODs are served this way and have no single playable URL:
    `media_url` alone is not decodable. Each segment is self-contained and
    carries absolute presentation timestamps, so seeking means fetching only
    the segments covering the wanted offset.
    """

    fragment_duration_s: float | None = None
    """Nominal seconds per segment, used to map an offset to a segment index."""

    @property
    def is_fragmented(self) -> bool:
        return bool(self.fragment_urls)


@dataclass(frozen=True)
class Frame:
    """One sampled video frame plus the context the vision pipeline needs."""

    index: int
    """Sample index, 0-based. Not the source frame number."""

    image: np.ndarray
    """HxWx3 uint8 BGR array - the layout OpenCV expects."""

    width: int
    height: int

    timestamp_s: float | None
    """Absolute presentation timestamp within the source, in seconds.

    The decoder runs FFmpeg with `-copyts`, so for archived video this is the
    true offset from the start of the recording and stays correct after
    seeking. For live streams it is an offset within the media segment window
    we opened, NOT a wall-clock broadcast time.
    """

    source_url: str
    source_id: str | None = None
    is_live: bool = False

    received_at: datetime | None = None
    """Wall-clock time the frame was read. For live sources this is the only
    clock tied to real time, and it lags the broadcast by the stream delay."""

    extra: dict[str, Any] = field(default_factory=dict)
