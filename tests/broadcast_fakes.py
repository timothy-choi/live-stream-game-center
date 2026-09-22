"""Shared fakes for M9 offline tests. No network, no YouTube, no PostgreSQL."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from lsgc.broadcast.errors import YouTubeApiError
from lsgc.broadcast.models import DiscoveredBroadcast, PlatformLifecycle
from lsgc.live.errors import SessionFailed
from lsgc.live.state import SessionHealth, SessionState
from lsgc.models import Frame
from lsgc.score.models import RawScoreObservation, ScoreRead


CHANNEL_A = "UC_CHANNEL_A"
CHANNEL_B = "UC_CHANNEL_B"


class DummyCalibration:
    def __init__(self, channel_id: str) -> None:
        self.profile = SimpleNamespace(channel_id=channel_id)
        self.reference = None
        self.glyphs = None
        self.finalization = None
        self.final_reference = None


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeCatalog:
    def __init__(
        self,
        broadcasts: list[DiscoveredBroadcast] | None = None,
        *,
        fail: bool = False,
        refresh: dict[str, DiscoveredBroadcast] | None = None,
    ) -> None:
        self.broadcasts = list(broadcasts or [])
        self.fail = fail
        self.refresh = dict(refresh or {})
        self.list_calls = 0
        self.refresh_calls: list[tuple[str, ...]] = []

    def list_channel_broadcasts(self, channel_id: str) -> tuple[DiscoveredBroadcast, ...]:
        self.list_calls += 1
        if self.fail:
            raise YouTubeApiError(
                "YouTube API HTTP 503: injected unavailable key=secret-token-value"
            )
        return tuple(self.broadcasts)

    def refresh_videos(self, video_ids) -> tuple[DiscoveredBroadcast, ...]:
        ids = tuple(video_ids)
        self.refresh_calls.append(ids)
        return tuple(
            self.refresh[item] for item in ids if item in self.refresh
        )


class QueueTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def get_json(self, path: str, params, headers):
        self.calls.append((path, dict(params), dict(headers)))
        if not self.responses:
            raise YouTubeApiError("no scripted YouTube response remaining")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeLiveSession:
    """M4-shaped session for orchestration tests."""

    def __init__(
        self,
        watch_url: str,
        *,
        frames: tuple[Frame, ...] = (),
        terminal: SessionState = SessionState.ENDED,
        hold: threading.Event | None = None,
        fail: bool = False,
    ) -> None:
        self.watch_url = watch_url
        self._frames = frames
        self._terminal = terminal
        self._hold = hold
        self._fail = fail
        self._cancel = threading.Event()
        self._state = SessionState.STARTING
        self._started = datetime.now(tz=UTC)
        self.frames_emitted = 0

    def cancel(self) -> None:
        self._cancel.set()
        if self._hold is not None:
            self._hold.set()

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def health(self) -> SessionHealth:
        return SessionHealth(
            state=self._state,
            watch_url=self.watch_url,
            source_id=None,
            interval_s=2.0,
            frames_emitted=self.frames_emitted,
            frames_skipped=0,
            resolve_attempts=1,
            reconnect_count=0,
            consecutive_failures=0,
            source_epoch=1,
            last_frame_at=None,
            last_pts_s=None,
            last_error="injected failure" if self._state is SessionState.FAILED else None,
            started_at=self._started,
            ended_at=None,
            longest_gap_s=None,
            last_gap_s=None,
        )

    def frames(self):
        self._state = SessionState.RUNNING
        for frame in self._frames:
            if self._cancel.is_set():
                self._state = SessionState.CANCELLED
                return
            self.frames_emitted += 1
            yield frame
        if self._hold is not None:
            while not self._cancel.is_set() and not self._hold.is_set():
                self._cancel.wait(0.01)
            if self._cancel.is_set():
                self._state = SessionState.CANCELLED
                return
        if self._fail or self._terminal is SessionState.FAILED:
            self._state = SessionState.FAILED
            raise SessionFailed("injected acquisition failure")
        self._state = self._terminal
        if self._cancel.is_set():
            self._state = SessionState.CANCELLED


class RecordingSessionFactory:
    def __init__(self, *, hold: threading.Event | None = None, **kwargs) -> None:
        self.hold = hold
        self.kwargs = kwargs
        self.created: list[str] = []
        self.sessions: list[FakeLiveSession] = []

    def __call__(self, watch_url: str) -> FakeLiveSession:
        self.created.append(watch_url)
        session = FakeLiveSession(watch_url, hold=self.hold, **self.kwargs)
        self.sessions.append(session)
        return session


class AbsencePipeline:
    def observe_frame(self, frame: Frame) -> RawScoreObservation | None:
        return None


def absence_pipeline_factory(_calibration) -> AbsencePipeline:
    return AbsencePipeline()


def discovered(
    broadcast_id: str,
    *,
    channel_id: str = CHANNEL_A,
    lifecycle: PlatformLifecycle = PlatformLifecycle.UPCOMING,
    title: str | None = "Game",
    scheduled_start: datetime | None = None,
    actual_start: datetime | None = None,
    actual_end: datetime | None = None,
) -> DiscoveredBroadcast:
    return DiscoveredBroadcast(
        broadcast_id=broadcast_id,
        channel_id=channel_id,
        lifecycle=lifecycle,
        title=title,
        scheduled_start=scheduled_start,
        actual_start=actual_start,
        actual_end=actual_end,
    )


def readable_obs(frame: Frame, left: int, right: int) -> RawScoreObservation:
    return RawScoreObservation(
        frame_index=frame.index,
        timestamp_s=frame.timestamp_s,
        received_at=frame.received_at,
        left=ScoreRead(
            value=left,
            readable=True,
            confidence=0.99,
            margin=0.2,
            raw_digits=str(left),
        ),
        right=ScoreRead(
            value=right,
            readable=True,
            confidence=0.99,
            margin=0.2,
            raw_digits=str(right),
        ),
        detection_present=True,
        detection_score=0.9,
        detection_threshold=0.55,
        channel_id=CHANNEL_A,
        profile_id="test",
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_id=frame.source_id,
        source_url=frame.source_url,
        source_epoch=1,
    )
