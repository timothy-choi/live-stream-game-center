"""Bounded retrospective recovery of missed gameplay scoreboard states.

Recovery answers one question from available video: which left/right numeric
pairs were actually visible? It does not infer scoring events from a point
difference and it does not encode a particular sport's scoring rules.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import cv2
import numpy as np

from lsgc.models import Frame

JPEG_QUALITY = 85
"""Lossy storage for the rolling window. Detection/reading already tolerate
compression; this keeps RAM bounded without retaining raw BGR."""


class RecoveryCoverage(str, Enum):
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    FULL = "FULL"


@dataclass(frozen=True)
class RecoveryWindow:
    """Frames available for one requested interval, plus coverage honesty."""

    frames: tuple[Frame, ...]
    coverage: RecoveryCoverage
    available_start_s: float | None
    available_end_s: float | None
    requested_start_s: float | None
    requested_end_s: float | None


class RecoverySource(Protocol):
    """Abstract replay. Finalization must not depend on YouTube seeking."""

    def scan(
        self,
        *,
        start_s: float | None,
        end_s: float | None,
        interval_s: float,
    ) -> RecoveryWindow: ...


@dataclass
class _StoredFrame:
    timestamp_s: float
    frame_index: int
    jpeg: bytes
    width: int
    height: int
    source_url: str
    source_id: str | None
    source_epoch: int | None
    received_at: object | None


class FrameSequenceSource:
    """RecoverySource over already-decoded frames.

    Used by tests and by optional seek adapters. Finalization talks only to
    `RecoverySource.scan`, never to a particular platform's seek API.
    """

    def __init__(self, frames: Sequence[Frame]) -> None:
        self._frames = tuple(sorted(frames, key=_frame_time))

    def scan(
        self,
        *,
        start_s: float | None,
        end_s: float | None,
        interval_s: float,
    ) -> RecoveryWindow:
        selected: list[Frame] = []
        last_emitted: float | None = None
        times = [frame.timestamp_s for frame in self._frames if frame.timestamp_s is not None]
        available_start = times[0] if times else None
        available_end = times[-1] if times else None
        for frame in self._frames:
            timestamp = frame.timestamp_s
            if timestamp is None:
                continue
            if start_s is not None and timestamp < start_s:
                continue
            if end_s is not None and timestamp > end_s:
                break
            if last_emitted is not None and interval_s > 0:
                if timestamp - last_emitted < interval_s:
                    continue
            selected.append(frame)
            last_emitted = timestamp
        coverage = _coverage(
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
            emitted=bool(selected),
        )
        return RecoveryWindow(
            frames=tuple(selected),
            coverage=coverage,
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
        )


class CompositeRecoverySource:
    """Prefer the rolling buffer; fill remaining coverage from a seek source."""

    def __init__(
        self,
        primary: RecoverySource,
        secondary: RecoverySource | None = None,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    def scan(
        self,
        *,
        start_s: float | None,
        end_s: float | None,
        interval_s: float,
    ) -> RecoveryWindow:
        first = self._primary.scan(start_s=start_s, end_s=end_s, interval_s=interval_s)
        if first.coverage is RecoveryCoverage.FULL or self._secondary is None:
            return first
        second = self._secondary.scan(start_s=start_s, end_s=end_s, interval_s=interval_s)
        merged: dict[float, Frame] = {}
        for frame in (*first.frames, *second.frames):
            timestamp = frame.timestamp_s
            if timestamp is None:
                continue
            merged[timestamp] = frame
        frames = tuple(merged[key] for key in sorted(merged))
        available_start = min(
            (item for item in (first.available_start_s, second.available_start_s) if item is not None),
            default=None,
        )
        available_end = max(
            (item for item in (first.available_end_s, second.available_end_s) if item is not None),
            default=None,
        )
        coverage = _coverage(
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
            emitted=bool(frames),
        )
        return RecoveryWindow(
            frames=frames,
            coverage=coverage,
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
        )


class MemoryFrameWindow:
    """Rolling JPEG buffer of recent live frames. Not unlimited raw video.

    Default retention is configured by the caller (`window_s`). Older frames
    are dropped as newer ones arrive. A later milestone can raise `window_s`
    without changing the scanner.
    """

    def __init__(self, window_s: float = 90.0) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self.window_s = float(window_s)
        self._items: deque[_StoredFrame] = deque()

    def __len__(self) -> int:
        return len(self._items)

    def push(self, frame: Frame) -> None:
        timestamp = frame.timestamp_s
        if timestamp is None or timestamp != timestamp:
            return
        ok, encoded = cv2.imencode(
            ".jpg", frame.image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        )
        if not ok:
            return
        extra = frame.extra or {}
        epoch = extra.get("source_epoch")
        self._items.append(
            _StoredFrame(
                timestamp_s=float(timestamp),
                frame_index=frame.index,
                jpeg=encoded.tobytes(),
                width=frame.width,
                height=frame.height,
                source_url=frame.source_url,
                source_id=frame.source_id,
                source_epoch=epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None,
                received_at=frame.received_at,
            )
        )
        self._evict(float(timestamp))

    def _evict(self, newest_s: float) -> None:
        cutoff = newest_s - self.window_s
        while self._items and self._items[0].timestamp_s < cutoff:
            self._items.popleft()

    def scan(
        self,
        *,
        start_s: float | None,
        end_s: float | None,
        interval_s: float,
    ) -> RecoveryWindow:
        selected: list[Frame] = []
        last_emitted: float | None = None
        available_start = self._items[0].timestamp_s if self._items else None
        available_end = self._items[-1].timestamp_s if self._items else None
        for item in self._items:
            if start_s is not None and item.timestamp_s < start_s:
                continue
            if end_s is not None and item.timestamp_s > end_s:
                break
            if last_emitted is not None and interval_s > 0:
                if item.timestamp_s - last_emitted < interval_s:
                    continue
            frame = _decode_stored(item)
            if frame is None:
                continue
            selected.append(frame)
            last_emitted = item.timestamp_s
        coverage = _coverage(
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
            emitted=bool(selected),
        )
        return RecoveryWindow(
            frames=tuple(selected),
            coverage=coverage,
            available_start_s=available_start,
            available_end_s=available_end,
            requested_start_s=start_s,
            requested_end_s=end_s,
        )


def _frame_time(frame: Frame) -> float:
    stamp = frame.timestamp_s
    return float("inf") if stamp is None else float(stamp)


def _decode_stored(item: _StoredFrame) -> Frame | None:
    image = cv2.imdecode(np.frombuffer(item.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    extra = {}
    if item.source_epoch is not None:
        extra["source_epoch"] = item.source_epoch
    return Frame(
        index=item.frame_index,
        image=image,
        width=item.width,
        height=item.height,
        timestamp_s=item.timestamp_s,
        source_url=item.source_url,
        source_id=item.source_id,
        received_at=item.received_at,  # type: ignore[arg-type]
        extra=extra,
    )


def _coverage(
    *,
    available_start_s: float | None,
    available_end_s: float | None,
    requested_start_s: float | None,
    requested_end_s: float | None,
    emitted: bool,
) -> RecoveryCoverage:
    if not emitted or available_start_s is None or available_end_s is None:
        return RecoveryCoverage.NONE
    start_ok = requested_start_s is None or available_start_s <= requested_start_s + 1e-6
    end_ok = requested_end_s is None or available_end_s >= requested_end_s - 1e-6
    if start_ok and end_ok:
        return RecoveryCoverage.FULL
    return RecoveryCoverage.PARTIAL
