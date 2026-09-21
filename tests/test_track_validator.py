"""Temporal score validator. Fully offline, table-driven sequences."""

from __future__ import annotations

import pytest

from lsgc.score.models import RawScoreObservation, ScoreRead, UnreadableReason
from lsgc.track import (
    ApplyOutcome,
    ConfirmationPolicy,
    PolicyError,
    TemporalScoreValidator,
)


def obs(
    left: int | None,
    right: int | None,
    *,
    index: int,
    ts: float,
    epoch: int = 1,
) -> RawScoreObservation:
    def side(value: int | None) -> ScoreRead:
        if value is None:
            return ScoreRead.unreadable(UnreadableReason.NO_DIGITS)
        return ScoreRead.of(value, confidence=0.99, margin=0.25, raw_digits=str(value))

    return RawScoreObservation(
        frame_index=index,
        timestamp_s=ts,
        received_at=None,
        left=side(left),
        right=side(right),
        detection_present=True,
        detection_score=0.99,
        detection_threshold=0.55,
        channel_id="UC_track",
        profile_id="track-test",
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_epoch=epoch,
    )


def feed(validator: TemporalScoreValidator, items: list) -> list:
    results = []
    for item in items:
        if item == "end":
            results.append(validator.end_acquisition())
        elif item == "reset":
            results.append(validator.reset())
        elif isinstance(item, tuple) and item and item[0] == "absent":
            _, index, ts, epoch = item
            results.append(
                validator.note_absence(frame_index=index, timestamp_s=ts, source_epoch=epoch)
            )
        else:
            results.append(validator.apply(item))
    return results


DEFAULT = ConfirmationPolicy()


class TestPolicy:
    def test_defaults_match_m4_cadence(self) -> None:
        policy = ConfirmationPolicy()
        assert policy.required_observations == 2
        assert policy.min_span_s == 2.0
        assert policy.max_candidate_gap_s == 8.0

    def test_rejects_single_observation_policy(self) -> None:
        with pytest.raises(PolicyError, match=">= 2"):
            ConfirmationPolicy(required_observations=1)


class TestAInitializationMidGame:
    def test_repeated_nonzero_pair_initializes(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        r1 = v.apply(obs(21, 14, index=0, ts=0.0))
        assert r1.outcome is ApplyOutcome.CANDIDATE_STARTED
        assert not r1.state.initialized
        r2 = v.apply(obs(21, 14, index=1, ts=2.0))
        assert r2.outcome is ApplyOutcome.CONFIRMED
        assert r2.state.pair == (21, 14)
        assert r2.change is not None and r2.change.initialization
        assert v.initializations == 1
        assert v.history[0].previous_pair is None

    def test_does_not_infer_zero_zero(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(21, 14, index=0, ts=0.0))
        assert v.state.pair is None
        assert v.state.left_score is None


class TestBOneFrameBadRead:
    def test_isolated_wrong_read_does_not_move_trusted(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=i, ts=float(i * 2)) for i in range(3)])
        assert v.state.pair == (14, 7)
        bad = v.apply(obs(14, 1, index=3, ts=6.0))
        assert bad.outcome is ApplyOutcome.CANDIDATE_STARTED
        assert v.state.pair == (14, 7)
        back = v.apply(obs(14, 7, index=4, ts=8.0))
        assert back.outcome is ApplyOutcome.CANDIDATE_RESET
        assert v.state.pair == (14, 7)
        v.apply(obs(14, 7, index=5, ts=10.0))
        assert v.state.pair == (14, 7)
        assert v.state.revision == 1


class TestCRealChange:
    def test_repeated_new_pair_confirms(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        assert v.state.pair == (14, 7)
        r1 = v.apply(obs(21, 7, index=2, ts=4.0))
        assert r1.outcome is ApplyOutcome.CANDIDATE_STARTED
        r2 = v.apply(obs(21, 7, index=3, ts=6.0))
        assert r2.outcome is ApplyOutcome.CONFIRMED
        assert v.state.pair == (21, 7)
        assert v.history[-1].previous_pair == (14, 7)
        assert v.history[-1].new_pair == (21, 7)


class TestDFlapping:
    def test_alternating_pairs_never_confirm(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        outcomes = []
        for i in range(2, 10):
            left, right = (21, 7) if i % 2 == 0 else (14, 7)
            outcomes.append(v.apply(obs(left, right, index=i, ts=float(i * 2))).outcome)
        assert v.state.pair == (14, 7)
        assert ApplyOutcome.CONFIRMED not in outcomes


class TestEAbsenceDuringScoring:
    def test_banner_preserves_trusted_and_does_not_zero(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        r = v.note_absence(frame_index=2, timestamp_s=4.0, source_epoch=1)
        assert r.outcome is ApplyOutcome.ABSENT
        assert v.state.pair == (14, 7)
        v.note_absence(frame_index=3, timestamp_s=6.0, source_epoch=1)
        v.apply(obs(14, 7, index=4, ts=8.0))
        assert v.state.pair == (14, 7)
        assert v.state.revision == 1


class TestFPartialReads:
    def test_partial_never_fabricates_or_confirms(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        r = v.apply(obs(14, None, index=0, ts=0.0))
        assert r.outcome is ApplyOutcome.IGNORED_PARTIAL
        assert not r.state.initialized
        v.apply(obs(14, None, index=1, ts=2.0))
        v.apply(obs(14, None, index=2, ts=4.0))
        assert v.state.pair is None
        # complete pair still needs its own evidence
        v.apply(obs(14, 7, index=3, ts=6.0))
        assert not v.state.initialized
        v.apply(obs(14, 7, index=4, ts=8.0))
        assert v.state.pair == (14, 7)


class TestGDuplicates:
    def test_duplicate_index_does_not_count(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(21, 14, index=0, ts=0.0))
        dup = v.apply(obs(21, 14, index=0, ts=0.0))
        assert dup.outcome is ApplyOutcome.IGNORED_DUPLICATE
        assert not v.state.initialized
        assert v.duplicates_ignored == 1

    def test_duplicate_epoch_pts_does_not_count(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(21, 14, index=0, ts=5.0, epoch=1))
        dup = v.apply(obs(21, 14, index=1, ts=5.0, epoch=1))
        assert dup.outcome is ApplyOutcome.IGNORED_DUPLICATE
        assert not v.state.initialized


class TestHOutOfOrder:
    def test_stale_index_does_not_regress(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(7, 0, index=0, ts=0.0), obs(7, 0, index=1, ts=2.0)])
        v.apply(obs(14, 0, index=2, ts=4.0))
        stale = v.apply(obs(99, 99, index=1, ts=2.0))
        assert stale.outcome is ApplyOutcome.IGNORED_STALE
        assert v.state.pair == (7, 0)
        assert v.stale_ignored == 1

    def test_stale_timestamp_same_epoch_ignored(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(7, 0, index=5, ts=10.0, epoch=1))
        stale = v.apply(obs(3, 3, index=6, ts=9.0, epoch=1))
        assert stale.outcome is ApplyOutcome.IGNORED_STALE
        assert not v.state.initialized


class TestISourceEpoch:
    def test_epoch_change_does_not_clear_trusted(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(
            v,
            [
                obs(10, 7, index=0, ts=0.0, epoch=1),
                obs(10, 7, index=1, ts=2.0, epoch=1),
            ],
        )
        assert v.state.pair == (10, 7)
        r = v.apply(obs(10, 7, index=2, ts=0.1, epoch=2))
        assert r.outcome is ApplyOutcome.MATCHES_TRUSTED
        assert v.state.pair == (10, 7)

    def test_epoch_change_resets_candidate_evidence(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(21, 7, index=0, ts=0.0, epoch=1))
        assert v.candidate is not None and v.candidate.evidence_count == 1
        r = v.apply(obs(21, 7, index=1, ts=0.2, epoch=2))
        assert r.outcome is ApplyOutcome.CANDIDATE_STARTED
        assert v.candidate is not None and v.candidate.evidence_count == 1
        assert not v.state.initialized


class TestJLargePtsGap:
    def test_gap_does_not_erase_trusted(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(3, 7, index=0, ts=0.0), obs(3, 7, index=1, ts=2.0)])
        r = v.apply(obs(3, 7, index=2, ts=40.0))
        assert v.state.pair == (3, 7)
        assert r.outcome is ApplyOutcome.MATCHES_TRUSTED

    def test_candidate_does_not_chain_across_large_gap(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(21, 14, index=0, ts=0.0))
        r = v.apply(obs(21, 14, index=1, ts=20.0))
        assert r.outcome is ApplyOutcome.CANDIDATE_STARTED
        assert not v.state.initialized
        v.apply(obs(21, 14, index=2, ts=22.0))
        assert v.state.pair == (21, 14)


class TestKBroadcastEnd:
    def test_end_preserves_score_and_has_no_final_semantics(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        end = v.end_acquisition()
        assert end.outcome is ApplyOutcome.FROZEN
        assert end.state.frozen
        assert end.state.pair == (14, 7)
        assert "FINAL" not in end.state.summary()
        assert "final" not in end.state.summary().lower()
        later = v.apply(obs(99, 99, index=2, ts=4.0))
        assert later.outcome is ApplyOutcome.FROZEN
        assert v.state.pair == (14, 7)


class TestLReset:
    def test_explicit_reset_clears_trusted_and_history(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        assert v.state.initialized
        r = v.reset()
        assert r.outcome is ApplyOutcome.RESET
        assert not v.state.initialized
        assert v.state.revision == 0
        assert v.history == ()
        assert v.candidate is None
        feed(v, [obs(0, 0, index=0, ts=0.0), obs(0, 0, index=1, ts=2.0)])
        assert v.state.pair == (0, 0)
        assert v.history[0].initialization


class TestMBeginAtNonzero:
    def test_application_start_mid_game(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(35, 21, index=10, ts=100.0), obs(35, 21, index=11, ts=102.0)])
        assert v.state.pair == (35, 21)
        assert v.history[0].initialization


class TestNMissedIntermediate:
    def test_confirms_newly_stable_pair_without_seeing_in_between(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(7, 0, index=0, ts=0.0), obs(7, 0, index=1, ts=2.0)])
        v.note_absence(frame_index=2, timestamp_s=20.0, source_epoch=1)
        r1 = v.apply(obs(21, 7, index=3, ts=22.0))
        assert r1.outcome is ApplyOutcome.CANDIDATE_STARTED
        assert v.state.pair == (7, 0)
        r2 = v.apply(obs(21, 7, index=4, ts=24.0))
        assert r2.outcome is ApplyOutcome.CONFIRMED
        assert v.state.pair == (21, 7)


class TestWrongReads:
    def test_nonconsecutive_wrong_values_do_not_accumulate(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        v.apply(obs(14, 1, index=2, ts=4.0))
        v.apply(obs(14, 7, index=3, ts=6.0))
        v.apply(obs(14, 1, index=4, ts=8.0))
        v.apply(obs(14, 7, index=5, ts=10.0))
        assert v.state.pair == (14, 7)
        assert v.state.revision == 1

    def test_sustained_wrong_value_can_be_confirmed(self) -> None:
        """M7 cannot know a stable visual/OCR value is false."""
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(14, 7, index=0, ts=0.0), obs(14, 7, index=1, ts=2.0)])
        v.apply(obs(14, 1, index=2, ts=4.0))
        v.apply(obs(14, 1, index=3, ts=6.0))
        assert v.state.pair == (14, 1)


class TestHistoryAndRevision:
    def test_revisions_are_monotonic_and_history_ordered(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(
            v,
            [
                obs(0, 0, index=0, ts=0.0),
                obs(0, 0, index=1, ts=2.0),
                obs(7, 0, index=2, ts=4.0),
                obs(7, 0, index=3, ts=6.0),
                obs(7, 7, index=4, ts=8.0),
                obs(7, 7, index=5, ts=10.0),
            ],
        )
        assert [c.revision for c in v.history] == [1, 2, 3]
        assert [c.new_pair for c in v.history] == [(0, 0), (7, 0), (7, 7)]
        assert v.state.revision == 3


class TestUnreadable:
    def test_both_unreadable_does_not_clear_trusted(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        feed(v, [obs(10, 3, index=0, ts=0.0), obs(10, 3, index=1, ts=2.0)])
        r = v.apply(obs(None, None, index=2, ts=4.0))
        assert r.outcome is ApplyOutcome.IGNORED_UNREADABLE
        assert v.state.pair == (10, 3)


class TestSingleObservationNeverConfirms:
    def test_one_readable_frame_is_not_trusted(self) -> None:
        v = TemporalScoreValidator(DEFAULT)
        v.apply(obs(62, 35, index=0, ts=0.0))
        assert not v.state.initialized
        assert v.state.revision == 0


class TestPipelineEpochPlumbing:
    def test_observe_frame_copies_source_epoch(self, detection_profile, scoreboard_image) -> None:
        from datetime import UTC, datetime

        from lsgc.models import Frame
        from lsgc.score import ScoreObservationPipeline, ScoreReader, build_glyphs
        from lsgc.vision import ScoreboardDetector, build_reference
        from tests.test_score_reader import _present_frame

        present = _present_frame(detection_profile, 4, 2)
        glyphs = build_glyphs(
            [
                (_present_frame(detection_profile, 0, 1), 0, 1),
                (_present_frame(detection_profile, 2, 3), 2, 3),
                (_present_frame(detection_profile, 4, 5), 4, 5),
                (_present_frame(detection_profile, 6, 7), 6, 7),
                (_present_frame(detection_profile, 8, 9), 8, 9),
                (_present_frame(detection_profile, 14, 28), 14, 28),
            ],
            detection_profile,
        )
        pipeline = ScoreObservationPipeline(
            ScoreboardDetector(detection_profile, build_reference([present], detection_profile)),
            ScoreReader(glyphs, min_score=0.5, min_margin=0.02),
        )
        frame = Frame(
            index=3,
            image=present,
            width=present.shape[1],
            height=present.shape[0],
            timestamp_s=12.0,
            source_url="https://example.invalid/x",
            received_at=datetime(2026, 9, 21, tzinfo=UTC),
            extra={"source_epoch": 4},
        )
        observation = pipeline.observe_frame(frame)
        assert observation is not None
        assert observation.source_epoch == 4
        assert observation.frame_index == 3
