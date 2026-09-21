"""Continuous acquisition of sampled Frames from one live YouTube broadcast.

The durable identity is the *watch URL*. Resolved CDN/media URLs are treated
as ephemeral: when decoding fails they are discarded and the watch URL is
resolved again. Downstream consumers see `Frame` objects only — never yt-dlp,
FFmpeg, or googlevideo URLs.

Logical time
------------
`Frame.timestamp_s` remains the source presentation timestamp. For live HLS
that is an offset on the media timeline FFmpeg opened, **not** YouTube
wall-clock. After a reconnect that timeline may continue or it may restart
near a different origin.

Downstream order is `Frame.index`, a session-monotonic sequence that only
counts emitted frames. Reconnect artifacts are not the consumer's problem:

- a sample whose PTS does not advance past the last emitted PTS is dropped
  (duplicate / small reorder);
- a PTS that jumps *backward* by more than one sample interval is treated as
  a new media-timeline origin (`source_epoch` increments) and is emitted as
  new progression, not as rewound time.

`Frame.received_at` is wall-clock receipt and is the only real-time clock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime

from lsgc.decoder import iter_continuous_frames
from lsgc.errors import DecodeError, SourceResolutionError
from lsgc.live.errors import SessionFailed
from lsgc.live.policy import RetryPolicy
from lsgc.live.sanitize import looks_like_broadcast_ended, sanitize_text
from lsgc.live.state import SessionHealth, SessionState
from lsgc.models import Frame, MediaSource
from lsgc.resolver import resolve_youtube

ResolverFn = Callable[..., MediaSource]
DecoderFn = Callable[..., Iterator[Frame]]
SleeperFn = Callable[[float, threading.Event], None]
ClockFn = Callable[[], datetime]


class _Halt(Exception):
    """Internal: a terminal state is already recorded; stop iterating."""


def _default_sleep(seconds: float, cancel: threading.Event) -> None:
    if seconds > 0:
        cancel.wait(timeout=seconds)


def _default_clock() -> datetime:
    return datetime.now(UTC)


class LiveFrameSession:
    """Long-running sampler for one explicitly supplied live watch URL.

    Construct, then iterate `frames()`. Call `cancel()` from another thread
    (or the same one) to stop. The iterator runs on the caller's thread; the
    only extra threads are the ones FFmpeg decoding already uses.
    """

    def __init__(
        self,
        watch_url: str,
        *,
        interval_s: float = 2.0,
        max_height: int = 720,
        retry: RetryPolicy | None = None,
        stall_timeout_s: float = 30.0,
        resolver: ResolverFn = resolve_youtube,
        decoder: DecoderFn = iter_continuous_frames,
        sleeper: SleeperFn = _default_sleep,
        clock: ClockFn = _default_clock,
        cancel: threading.Event | None = None,
    ) -> None:
        if not watch_url or not watch_url.strip():
            raise ValueError("watch_url must not be empty")
        if interval_s <= 0 or interval_s != interval_s:
            raise ValueError(f"interval_s must be > 0, got {interval_s!r}")
        if stall_timeout_s <= 0:
            raise ValueError(f"stall_timeout_s must be > 0, got {stall_timeout_s!r}")

        self._watch_url = watch_url.strip()
        self._interval_s = float(interval_s)
        self._max_height = max_height
        self._retry = retry or RetryPolicy()
        self._stall_timeout_s = stall_timeout_s
        self._resolver = resolver
        self._decoder = decoder
        self._sleeper = sleeper
        self._clock = clock
        self._cancel = cancel if cancel is not None else threading.Event()
        self._lock = threading.Lock()

        now = self._clock()
        self._state = SessionState.STARTING
        self._source_id: str | None = None
        self._frames_emitted = 0
        self._frames_skipped = 0
        self._resolve_attempts = 0
        self._reconnect_count = 0
        self._consecutive_failures = 0
        self._source_epoch = 0
        self._last_frame_at: datetime | None = None
        self._last_pts_s: float | None = None
        self._last_error: str | None = None
        self._started_at = now
        self._ended_at: datetime | None = None
        self._longest_gap_s: float | None = None
        self._last_gap_s: float | None = None
        self._saw_live = False
        self._started_iterating = False

    # -- public ------------------------------------------------------------

    def cancel(self) -> None:
        """Request a clean stop. Safe to call from another thread."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def health(self) -> SessionHealth:
        with self._lock:
            return SessionHealth(
                state=self._state,
                watch_url=self._watch_url,
                source_id=self._source_id,
                interval_s=self._interval_s,
                frames_emitted=self._frames_emitted,
                frames_skipped=self._frames_skipped,
                resolve_attempts=self._resolve_attempts,
                reconnect_count=self._reconnect_count,
                consecutive_failures=self._consecutive_failures,
                source_epoch=self._source_epoch,
                last_frame_at=self._last_frame_at,
                last_pts_s=self._last_pts_s,
                last_error=self._last_error,
                started_at=self._started_at,
                ended_at=self._ended_at,
                longest_gap_s=self._longest_gap_s,
                last_gap_s=self._last_gap_s,
            )

    def frames(self) -> Iterator[Frame]:
        """Yield sampled Frames until ENDED, FAILED, or CANCELLED.

        One consumer. FAILED raises `SessionFailed`; the other terminal
        states simply stop iteration. After a terminal state, further
        iteration yields nothing.
        """
        with self._lock:
            if self._started_iterating:
                raise RuntimeError("LiveFrameSession.frames() is not re-entrant")
            self._started_iterating = True

        try:
            yield from self._run()
        finally:
            if not self.state.is_terminal:
                # Generator closed early (break, GeneratorExit): treat as cancel.
                self.cancel()
                self._finish(SessionState.CANCELLED)

    # -- state machine -----------------------------------------------------

    def _run(self) -> Iterator[Frame]:
        had_connection = False
        while not self._cancel.is_set():
            if had_connection:
                self._set_state(SessionState.RECOVERING)
            else:
                self._set_state(SessionState.STARTING)

            try:
                source = self._resolve()
            except SessionFailed:
                raise
            except _Halt:
                return

            self._set_state(SessionState.RUNNING)
            emitted_before = self._frames_emitted
            try:
                yield from self._drain_source(source)
            except DecodeError as exc:
                self._note_error(exc)
                if self._cancel.is_set():
                    self._finish(SessionState.CANCELLED)
                    return
                self._consecutive_failures += 1
                if self._retry.exhausted(self._consecutive_failures):
                    self._finish(SessionState.FAILED)
                    raise SessionFailed(
                        sanitize_text(
                            f"Retry budget exhausted after {self._consecutive_failures} "
                            f"consecutive failures: {exc}"
                        )
                        or "Retry budget exhausted"
                    ) from exc
                if had_connection:
                    self._reconnect_count += 1
                self._backoff()
                had_connection = True
                continue

            if self._cancel.is_set():
                self._finish(SessionState.CANCELLED)
                return

            # Decoder returned: media connection ended. That is not by itself
            # the broadcast ending — re-resolve the watch URL to find out.
            if self._frames_emitted == emitted_before:
                self._consecutive_failures += 1
                self._note_error("decoder produced no frames")
                if self._retry.exhausted(self._consecutive_failures):
                    self._finish(SessionState.FAILED)
                    raise SessionFailed(
                        "Retry budget exhausted: decoder produced no frames "
                        f"on {self._consecutive_failures} consecutive attempts"
                    )
            else:
                self._consecutive_failures = 0
                self._note_error("media connection ended")

            if had_connection or self._frames_emitted:
                self._reconnect_count += 1
            had_connection = True
            self._set_state(SessionState.RECOVERING)
            self._backoff()

        self._finish(SessionState.CANCELLED)

    def _resolve(self) -> MediaSource:
        while not self._cancel.is_set():
            self._resolve_attempts += 1
            try:
                source = self._resolver(self._watch_url, max_height=self._max_height)
            except TypeError:
                source = self._resolver(self._watch_url)
            except SourceResolutionError as exc:
                self._note_error(exc)
                if looks_like_broadcast_ended(str(exc)):
                    self._finish(SessionState.ENDED)
                    raise _Halt from exc
                self._consecutive_failures += 1
                if self._retry.exhausted(self._consecutive_failures):
                    self._finish(SessionState.FAILED)
                    raise SessionFailed(
                        sanitize_text(
                            f"Retry budget exhausted after {self._consecutive_failures} "
                            f"resolve failures: {exc}"
                        )
                        or "Retry budget exhausted"
                    ) from exc
                self._set_state(SessionState.RECOVERING)
                self._backoff()
                continue

            self._source_id = source.source_id
            if not source.is_live:
                if self._saw_live:
                    # Was live, now a VOD or unavailable as live: the broadcast
                    # ended. Acquisition ends; this is not a game-final signal.
                    self._finish(SessionState.ENDED)
                    raise _Halt
                self._note_error("resolved source is not currently live")
                self._finish(SessionState.FAILED)
                raise SessionFailed(
                    "Resolved source is not currently live. M4 acquires one "
                    "explicit live broadcast; pass a live watch URL."
                )
            self._saw_live = True
            self._source_epoch += 1
            return source

        self._finish(SessionState.CANCELLED)
        raise _Halt

    def _drain_source(self, source: MediaSource) -> Iterator[Frame]:
        origin_reset_s = max(self._interval_s, 1.0)
        stream = self._decoder(
            source,
            interval_s=self._interval_s,
            cancel=self._cancel,
            stall_timeout_s=self._stall_timeout_s,
        )
        for incoming in stream:
            if self._cancel.is_set():
                return
            pts = incoming.timestamp_s
            if not self._should_emit(pts, origin_reset_s):
                self._frames_skipped += 1
                continue
            outgoing = self._rewrite(incoming)
            self._record_emission(outgoing)
            yield outgoing

    def _should_emit(self, pts: float | None, origin_reset_s: float) -> bool:
        last = self._last_pts_s
        if pts is None or last is None:
            return True
        if pts > last + 1e-3:
            return True
        # Same PTS or a small step backward: duplicate / reorder.
        if last - pts <= origin_reset_s:
            return False
        # Large backward jump: new media-timeline origin, not rewound time.
        self._source_epoch += 1
        return True

    def _rewrite(self, frame: Frame) -> Frame:
        extra = dict(frame.extra)
        extra.pop("media_url", None)
        extra["source_epoch"] = self._source_epoch
        extra["watch_url"] = self._watch_url
        return replace(
            frame,
            index=self._frames_emitted,
            source_url=self._watch_url,
            extra=extra,
        )

    def _record_emission(self, frame: Frame) -> None:
        now = frame.received_at or self._clock()
        if self._last_frame_at is not None:
            gap = (now - self._last_frame_at).total_seconds()
            self._last_gap_s = gap
            if self._longest_gap_s is None or gap > self._longest_gap_s:
                self._longest_gap_s = gap
        self._frames_emitted += 1
        self._last_frame_at = now
        if frame.timestamp_s is not None:
            self._last_pts_s = frame.timestamp_s
        self._consecutive_failures = 0
        self._last_error = None

    def _backoff(self) -> None:
        if self._cancel.is_set():
            return
        delay = self._retry.backoff_s(max(1, self._consecutive_failures))
        self._sleeper(delay, self._cancel)

    def _note_error(self, exc: BaseException | str) -> None:
        self._last_error = sanitize_text(str(exc))

    def _set_state(self, state: SessionState) -> None:
        with self._lock:
            if self._state.is_terminal:
                return
            self._state = state

    def _finish(self, state: SessionState) -> None:
        with self._lock:
            if self._state.is_terminal:
                return
            self._state = state
            self._ended_at = self._clock()
