"""LiveFrameSession orchestration. Resolver/decoder/clock are faked; the
session state machine under test is real. Completely offline.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from lsgc.errors import DecodeError, SourceResolutionError
from lsgc.live import (
    LiveFrameSession,
    RetryPolicy,
    SessionFailed,
    SessionState,
)
from lsgc.models import Frame, MediaSource

WATCH = "https://www.youtube.com/watch?v=livevid"
SECRET_MEDIA = (
    "https://rr1---sn-abc.googlevideo.com/videoplayback?expire=123&signature=secretvalue"
)


def _image(seed: int = 0) -> np.ndarray:
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[..., 0] = seed % 255
    image[..., 1] = 40
    return image


def make_frame(
    index: int,
    pts: float | None,
    *,
    received: datetime | None = None,
    image: np.ndarray | None = None,
) -> Frame:
    return Frame(
        index=index,
        image=_image(index) if image is None else image,
        width=32 if image is None else image.shape[1],
        height=24 if image is None else image.shape[0],
        timestamp_s=pts,
        source_url=WATCH,
        source_id="livevid",
        is_live=True,
        received_at=received or datetime(2026, 6, 1, tzinfo=UTC) + timedelta(seconds=index),
        extra={"protocol": "m3u8_native"},
    )


def live_source(*, media: str = "https://example.invalid/playlist.m3u8") -> MediaSource:
    return MediaSource(
        source_url=WATCH,
        media_url=media,
        is_live=True,
        source_id="livevid",
        protocol="m3u8_native",
    )


def vod_source() -> MediaSource:
    return MediaSource(
        source_url=WATCH,
        media_url="https://example.invalid/vod.mp4",
        is_live=False,
        source_id="livevid",
        protocol="https",
    )


class ScriptedResolver:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> MediaSource:
        self.calls.append(url)
        if not self.responses:
            raise SourceResolutionError("script exhausted")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, MediaSource)
        return item


class ScriptedDecoder:
    def __init__(self, connections: list[list[object]]) -> None:
        self.connections = list(connections)
        self.calls: list[MediaSource] = []

    def __call__(self, source: MediaSource, *, interval_s: float, cancel, stall_timeout_s=30.0):
        self.calls.append(source)

        def gen():
            if not self.connections:
                raise DecodeError("no scripted decoder connection")
            script = self.connections.pop(0)
            for item in script:
                if cancel is not None and cancel.is_set():
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item

        return gen()


def _zero_retry() -> RetryPolicy:
    return RetryPolicy(max_attempts=4, initial_backoff_s=0.0, max_backoff_s=0.0, multiplier=1.0)


def _session(resolver, decoder, **kwargs) -> LiveFrameSession:
    kwargs.setdefault("retry", _zero_retry())
    kwargs.setdefault("sleeper", lambda seconds, cancel: None)
    return LiveFrameSession(
        WATCH,
        interval_s=2.0,
        resolver=resolver,
        decoder=decoder,
        **kwargs,
    )


class TestNormalDelivery:
    def test_emits_monotonic_sampled_frames(self) -> None:
        resolver = ScriptedResolver([live_source(), vod_source()])
        decoder = ScriptedDecoder([[make_frame(i, float(i) * 2) for i in range(5)]])
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert [f.index for f in frames] == [0, 1, 2, 3, 4]
        assert [f.timestamp_s for f in frames] == [0.0, 2.0, 4.0, 6.0, 8.0]
        assert session.state is SessionState.ENDED
        assert session.health.frames_emitted == 5
        assert session.health.resolve_attempts == 2
        assert session.health.source_id == "livevid"
        assert resolver.calls == [WATCH, WATCH]

    def test_preserves_watch_url_not_media_url(self) -> None:
        source = live_source(media=SECRET_MEDIA)
        resolver = ScriptedResolver([source, vod_source()])
        decoder = ScriptedDecoder([[make_frame(0, 1.0)]])
        session = _session(resolver, decoder)
        frame = next(iter(session.frames()))
        assert frame.source_url == WATCH
        assert SECRET_MEDIA not in str(frame.extra)
        assert "media_url" not in frame.extra


class TestDecoderInterruption:
    def test_recovers_after_decode_failure_and_resumes(self) -> None:
        resolver = ScriptedResolver([live_source(), live_source(), vod_source()])
        decoder = ScriptedDecoder(
            [
                [make_frame(0, 1.0), DecodeError("socket reset")],
                [make_frame(0, 3.0), make_frame(1, 5.0)],
            ]
        )
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert [f.timestamp_s for f in frames] == [1.0, 3.0, 5.0]
        assert [f.index for f in frames] == [0, 1, 2]
        assert session.health.reconnect_count >= 1
        assert session.state is SessionState.ENDED


class TestReresolution:
    def test_same_watch_url_is_resolved_again_after_source_dies(self) -> None:
        first = live_source(media="https://example.invalid/a.m3u8")
        second = live_source(media="https://example.invalid/b.m3u8")
        resolver = ScriptedResolver([first, second, vod_source()])
        decoder = ScriptedDecoder(
            [
                [make_frame(0, 1.0)],
                [make_frame(0, 4.0)],
            ]
        )
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert [f.timestamp_s for f in frames] == [1.0, 4.0]
        assert resolver.calls == [WATCH, WATCH, WATCH]
        assert decoder.calls[0] is first
        assert decoder.calls[1] is second
        assert session.health.resolve_attempts == 3
        assert session.health.source_epoch >= 2


class TestDuplicateProtection:
    def test_overlapping_pts_after_reconnect_is_not_emitted(self) -> None:
        resolver = ScriptedResolver([live_source(), live_source(), vod_source()])
        decoder = ScriptedDecoder(
            [
                [make_frame(0, 1.0), make_frame(1, 2.0)],
                [make_frame(0, 2.0), make_frame(1, 3.0)],
            ]
        )
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert [f.timestamp_s for f in frames] == [1.0, 2.0, 3.0]
        assert session.health.frames_skipped == 1
        assert [f.index for f in frames] == [0, 1, 2]


class TestBackwardTimestamp:
    def test_origin_reset_is_emitted_as_new_progression(self) -> None:
        resolver = ScriptedResolver([live_source(), live_source(), vod_source()])
        decoder = ScriptedDecoder(
            [
                [make_frame(0, 100.0), make_frame(1, 102.0)],
                [make_frame(0, 0.5), make_frame(1, 2.5)],
            ]
        )
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert [f.timestamp_s for f in frames] == [100.0, 102.0, 0.5, 2.5]
        assert [f.index for f in frames] == [0, 1, 2, 3]
        epochs = [f.extra["source_epoch"] for f in frames]
        assert epochs[2] > epochs[1]


class TestResolverTransientFailure:
    def test_retries_then_succeeds(self) -> None:
        slept: list[float] = []

        def sleeper(seconds: float, cancel: threading.Event) -> None:
            slept.append(seconds)

        resolver = ScriptedResolver(
            [
                SourceResolutionError("temporary timeout"),
                SourceResolutionError("temporary timeout"),
                live_source(),
                vod_source(),
            ]
        )
        decoder = ScriptedDecoder([[make_frame(0, 1.0)]])
        policy = RetryPolicy(max_attempts=5, initial_backoff_s=1.0, max_backoff_s=10.0, multiplier=2.0)
        session = _session(resolver, decoder, retry=policy, sleeper=sleeper)
        frames = list(session.frames())
        assert [f.timestamp_s for f in frames] == [1.0]
        assert slept[:2] == [1.0, 2.0]
        assert session.state is SessionState.ENDED


class TestRetryExhaustion:
    def test_session_transitions_failed(self) -> None:
        resolver = ScriptedResolver(
            [SourceResolutionError("timeout") for _ in range(3)]
        )
        decoder = ScriptedDecoder([])
        session = _session(
            resolver,
            decoder,
            retry=RetryPolicy(max_attempts=3, initial_backoff_s=0.0, max_backoff_s=0.0),
        )
        with pytest.raises(SessionFailed, match="Retry budget exhausted"):
            list(session.frames())
        assert session.state is SessionState.FAILED
        assert session.health.resolve_attempts == 3
        assert session.health.frames_emitted == 0


class TestCancellation:
    def test_cancel_during_running_stops_and_emits_nothing_more(self) -> None:
        resolver = ScriptedResolver([live_source()])
        decoder = ScriptedDecoder([[make_frame(i, float(i)) for i in range(20)]])
        session = _session(resolver, decoder)
        seen: list[int] = []
        for frame in session.frames():
            seen.append(frame.index)
            if len(seen) == 2:
                session.cancel()
        assert seen == [0, 1]
        assert session.state is SessionState.CANCELLED
        assert session.health.frames_emitted == 2

    def test_cancel_during_backoff(self) -> None:
        cancel = threading.Event()

        def sleeper(seconds: float, event: threading.Event) -> None:
            event.set()

        resolver = ScriptedResolver([SourceResolutionError("temporary")])
        decoder = ScriptedDecoder([])
        session = LiveFrameSession(
            WATCH,
            interval_s=2.0,
            resolver=resolver,
            decoder=decoder,
            retry=RetryPolicy(max_attempts=8, initial_backoff_s=1.0, max_backoff_s=1.0),
            sleeper=sleeper,
            cancel=cancel,
        )
        assert list(session.frames()) == []
        assert session.state is SessionState.CANCELLED

    def test_frames_after_cancel_iterator_is_empty(self) -> None:
        resolver = ScriptedResolver([live_source()])
        decoder = ScriptedDecoder([[make_frame(0, 1.0), make_frame(1, 2.0)]])
        session = _session(resolver, decoder)
        iterator = session.frames()
        assert next(iterator).index == 0
        session.cancel()
        remaining = list(iterator)
        assert remaining == []
        assert session.state is SessionState.CANCELLED


class TestBroadcastEnding:
    def test_ended_live_event_message(self) -> None:
        resolver = ScriptedResolver(
            [
                live_source(),
                SourceResolutionError("ERROR: This live event has ended."),
            ]
        )
        decoder = ScriptedDecoder([[make_frame(0, 1.0)]])
        session = _session(resolver, decoder)
        frames = list(session.frames())
        assert len(frames) == 1
        assert session.state is SessionState.ENDED

    def test_re_resolve_to_vod_ends_acquisition(self) -> None:
        resolver = ScriptedResolver([live_source(), vod_source()])
        decoder = ScriptedDecoder([[make_frame(0, 1.0)]])
        session = _session(resolver, decoder)
        list(session.frames())
        assert session.state is SessionState.ENDED

    def test_never_live_fails_rather_than_pretending_the_broadcast_ended(self) -> None:
        resolver = ScriptedResolver([vod_source()])
        decoder = ScriptedDecoder([])
        session = _session(resolver, decoder)
        with pytest.raises(SessionFailed, match="not currently live"):
            list(session.frames())
        assert session.state is SessionState.FAILED


class TestHealthAndCadence:
    def test_health_tracks_gaps_and_interval(self) -> None:
        t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        resolver = ScriptedResolver([live_source(), vod_source()])
        decoder = ScriptedDecoder(
            [
                [
                    make_frame(0, 1.0, received=t0),
                    make_frame(1, 3.0, received=t0 + timedelta(seconds=2.5)),
                    make_frame(2, 5.0, received=t0 + timedelta(seconds=7.5)),
                ]
            ]
        )
        session = _session(resolver, decoder)
        list(session.frames())
        health = session.health
        assert health.interval_s == 2.0
        assert health.last_gap_s == pytest.approx(5.0)
        assert health.longest_gap_s == pytest.approx(5.0)
        assert health.frames_emitted == 3

    def test_states_include_starting_running_recovering(self) -> None:
        seen: list[SessionState] = []
        resolver = ScriptedResolver(
            [SourceResolutionError("blip"), live_source(), vod_source()]
        )
        decoder = ScriptedDecoder([[make_frame(0, 1.0)]])

        original = LiveFrameSession._set_state

        def tracking(self, state):  # type: ignore[no-untyped-def]
            original(self, state)
            seen.append(state)

        session = _session(resolver, decoder)
        session._set_state = tracking.__get__(session, LiveFrameSession)  # type: ignore[method-assign]
        list(session.frames())
        assert SessionState.STARTING in seen
        assert SessionState.RECOVERING in seen
        assert SessionState.RUNNING in seen
        assert session.state is SessionState.ENDED


class TestSecretsAreRedacted:
    def test_signed_media_url_does_not_appear_in_diagnostics(self) -> None:
        resolver = ScriptedResolver(
            [
                live_source(media=SECRET_MEDIA),
                live_source(media=SECRET_MEDIA),
                vod_source(),
            ]
        )
        decoder = ScriptedDecoder(
            [
                [make_frame(0, 1.0), DecodeError(f"FFmpeg failed opening {SECRET_MEDIA}")],
                [make_frame(0, 4.0)],
            ]
        )
        session = _session(resolver, decoder)
        list(session.frames())
        health = session.health
        blob = health.summary() + (health.last_error or "")
        assert "googlevideo" not in blob
        assert "secretvalue" not in blob
        assert "signature=" not in blob.lower()
        assert health.watch_url == WATCH


class TestM3ConsumerCompatibility:
    def test_emitted_frames_are_accepted_by_scoreboard_detector(
        self,
        detection_profile,
        scoreboard_image,
        scoreboard_reference,
    ) -> None:
        from lsgc.vision import ScoreboardDetector

        frame = make_frame(0, 1.0, image=scoreboard_image)
        # make_frame overwrote width/height from image in this helper
        frame = Frame(
            index=0,
            image=scoreboard_image,
            width=scoreboard_image.shape[1],
            height=scoreboard_image.shape[0],
            timestamp_s=1.0,
            source_url=WATCH,
            source_id="livevid",
            is_live=True,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
            extra={},
        )
        resolver = ScriptedResolver([live_source(), vod_source()])
        decoder = ScriptedDecoder([[frame]])
        session = _session(resolver, decoder)
        emitted = list(session.frames())
        assert len(emitted) == 1
        detector = ScoreboardDetector(
            detection_profile, scoreboard_reference, expect_channel="UC_detect_channel"
        )
        result = detector.detect_frame(emitted[0])
        assert result.present is True
        assert "LiveFrameSession" not in type(detector).__module__


class TestConstruction:
    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError, match="watch_url"):
            LiveFrameSession("  ", resolver=lambda u, **k: live_source(), decoder=lambda *a, **k: iter(()))

    def test_rejects_non_positive_interval(self) -> None:
        with pytest.raises(ValueError, match="interval_s"):
            LiveFrameSession(WATCH, interval_s=0)

    def test_frames_is_not_reentrant(self) -> None:
        resolver = ScriptedResolver([live_source()])
        decoder = ScriptedDecoder([[make_frame(i, float(i)) for i in range(5)]])
        session = _session(resolver, decoder)
        iterator = session.frames()
        next(iterator)
        with pytest.raises(RuntimeError, match="re-entrant"):
            list(session.frames())
        session.cancel()
        list(iterator)
