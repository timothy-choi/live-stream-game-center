"""Seeking and fragmented-source tests.

All offline. The fragmented cases feed locally generated MPEG-TS segments
through the same code path that serves YouTube's DASH segments.
"""

from __future__ import annotations

import pytest

from lsgc.decoder import _fragment_range, _sample_filter, iter_frames
from lsgc.models import MediaSource

from conftest import SEGMENT_COUNT, SEGMENT_DURATION_S


class TestSampleFilter:
    def test_uses_select_not_fps(self) -> None:
        """`fps` restamps frames onto a grid; `select` keeps true timestamps."""
        built = _sample_filter(10.0, 2.0)
        assert built.startswith("select=")
        assert "fps=" not in built

    def test_encodes_start_and_interval(self) -> None:
        built = _sample_filter(10.0, 2.0)
        assert "gte(t\\,10.0)" in built
        assert "gte(t-prev_selected_t\\,2.0)" in built

    def test_zero_interval_drops_spacing_clause(self) -> None:
        built = _sample_filter(4.0, 0)
        assert "prev_selected_t" not in built
        assert "gte(t\\,4.0)" in built

    def test_commas_inside_expressions_are_escaped(self) -> None:
        """An unescaped comma would split the filter chain and break FFmpeg."""
        for segment in _sample_filter(1.0, 1.0).split(","):
            assert not segment.startswith("prev_selected_t")


class TestFragmentRange:
    def test_starts_one_segment_early(self, fragmented_source: MediaSource) -> None:
        """Covers sources whose timeline does not begin at zero."""
        assert _fragment_range(fragmented_source, 8.0, 1, 0).start == 3

    def test_never_starts_below_zero(self, fragmented_source: MediaSource) -> None:
        assert _fragment_range(fragmented_source, 0.0, 1, 0).start == 0

    def test_span_grows_with_samples(self, fragmented_source: MediaSource) -> None:
        narrow = _fragment_range(fragmented_source, 0.0, 1, 0)
        wide = _fragment_range(fragmented_source, 0.0, 5, 4.0)
        assert len(wide) > len(narrow)

    def test_clamped_to_available_segments(self, fragmented_source: MediaSource) -> None:
        wide = _fragment_range(fragmented_source, 0.0, 1000, 60.0)
        assert wide.stop <= SEGMENT_COUNT
        assert len(wide) <= SEGMENT_COUNT

    def test_is_bounded_for_huge_requests(self) -> None:
        """A large request must not turn into a whole-video download."""
        huge = MediaSource(
            source_url="u",
            media_url="m",
            is_live=False,
            fragment_urls=tuple(f"f{i}" for i in range(100_000)),
            fragment_duration_s=5.0,
        )
        assert len(_fragment_range(huge, 0.0, 100_000, 5.0)) <= 40

    def test_never_empty(self, fragmented_source: MediaSource) -> None:
        assert len(_fragment_range(fragmented_source, 999_999.0, 1, 0)) >= 1


class TestSeekValidation:
    @pytest.mark.parametrize("bad", [-1.0, -0.001, float("nan")])
    def test_rejects_invalid_offsets(self, local_source: MediaSource, bad: float) -> None:
        with pytest.raises(ValueError):
            list(iter_frames(local_source, count=1, start_at_s=bad))

    def test_zero_offset_is_valid(self, local_source: MediaSource) -> None:
        frames = list(iter_frames(local_source, count=1, interval_s=0, start_at_s=0.0))
        assert len(frames) == 1


class TestSeekOnLocalFile:
    def test_seek_lands_on_requested_offset(self, local_source: MediaSource) -> None:
        frame = next(iter(iter_frames(local_source, count=1, interval_s=0, start_at_s=3.0)))
        assert frame.timestamp_s == pytest.approx(3.0, abs=0.1)

    def test_timestamps_stay_absolute_after_seeking(self, local_source: MediaSource) -> None:
        """The whole point of -copyts: offsets must not restart at zero."""
        frames = list(iter_frames(local_source, count=2, interval_s=1.0, start_at_s=4.0))
        assert [f.timestamp_s for f in frames] == pytest.approx([4.0, 5.0], abs=0.1)

    def test_requested_offset_travels_with_the_frame(self, local_source: MediaSource) -> None:
        frame = next(iter(iter_frames(local_source, count=1, interval_s=0, start_at_s=2.0)))
        assert frame.extra["requested_offset_s"] == 2.0

    def test_offset_past_end_fails_as_decode_error(self, local_source: MediaSource) -> None:
        from lsgc.errors import DecodeError

        with pytest.raises(DecodeError):
            list(iter_frames(local_source, count=1, start_at_s=9999.0))


class TestFragmentedDecoding:
    def test_decodes_from_segments(
        self, fragmented_source: MediaSource, served_fragments: list[int]
    ) -> None:
        frames = list(iter_frames(fragmented_source, count=2, interval_s=1.0, start_at_s=0.0))
        assert len(frames) == 2
        assert all(f.image.shape == (240, 320, 3) for f in frames)

    def test_seeking_skips_earlier_segments(
        self, fragmented_source: MediaSource, served_fragments: list[int]
    ) -> None:
        """A mid-recording sample must not fetch the whole recording."""
        list(iter_frames(fragmented_source, count=1, interval_s=0, start_at_s=12.0))
        fetched = [i for i in served_fragments if i != 0]  # 0 is the ffprobe geometry check
        assert fetched, "expected at least one segment fetch"
        assert min(fetched) >= 4
        assert len(set(fetched)) < SEGMENT_COUNT

    def test_bounded_sampling_over_segments(
        self, fragmented_source: MediaSource, served_fragments: list[int]
    ) -> None:
        frames = list(iter_frames(fragmented_source, count=3, interval_s=2.0, start_at_s=4.0))
        assert len(frames) == 3
        timestamps = [f.timestamp_s for f in frames]
        assert timestamps == sorted(timestamps)
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        assert all(gap == pytest.approx(SEGMENT_DURATION_S, abs=0.2) for gap in gaps)

    def test_fragment_fetch_failure_is_an_acquisition_error(
        self, fragmented_source: MediaSource, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YouTube refusing a segment must not be reported as a decoder fault."""
        from lsgc import decoder
        from lsgc.errors import SourceResolutionError

        def refuse(source, index, *, timeout_s=30.0):
            raise SourceResolutionError("HTTP 403 Forbidden")

        monkeypatch.setattr(decoder, "fetch_fragment", refuse)
        with pytest.raises(SourceResolutionError):
            list(iter_frames(fragmented_source, count=1))

    def test_is_fragmented_flag(self, fragmented_source: MediaSource, local_source: MediaSource) -> None:
        assert fragmented_source.is_fragmented is True
        assert local_source.is_fragmented is False
