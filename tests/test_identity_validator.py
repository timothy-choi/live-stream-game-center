"""Temporal matchup confirmation. Fully offline, table-driven sequences."""

from __future__ import annotations

import pytest

from lsgc.identity.models import (
    ConfirmedMatchupState,
    IdentityMethod,
    MatchupApplyOutcome,
    MatchupObservation,
    TeamIdentityObservation,
    UnreadableIdentityReason,
)
from lsgc.identity.policy import MatchupConfirmationPolicy
from lsgc.identity.errors import IdentityPolicyError, IdentityRestoreError
from lsgc.identity.validator import TemporalMatchupValidator


def side(team_id: str | None) -> TeamIdentityObservation:
    if team_id is None:
        return TeamIdentityObservation.unknown(UnreadableIdentityReason.LOW_CONFIDENCE)
    return TeamIdentityObservation.known(
        team_id, method=IdentityMethod.LOGO, confidence=0.9, margin=0.3
    )


def obs(
    left: str | None,
    right: str | None,
    *,
    index: int,
    ts: float,
    epoch: int = 1,
) -> MatchupObservation:
    return MatchupObservation(
        frame_index=index,
        timestamp_s=ts,
        received_at=None,
        left=side(left),
        right=side(right),
        detection_present=True,
        detection_score=0.99,
        detection_threshold=0.55,
        channel_id="UC_id",
        profile_id="id-test",
        profile_revision=1,
        frame_width=1280,
        frame_height=720,
        source_epoch=epoch,
    )


DEFAULT = MatchupConfirmationPolicy()


class TestPolicy:
    def test_defaults_require_two_observations_over_two_seconds(self) -> None:
        policy = MatchupConfirmationPolicy()
        assert policy.required_observations == 2
        assert policy.min_span_s == 2.0
        assert policy.max_candidate_gap_s == 8.0

    def test_rejects_single_observation_policy(self) -> None:
        with pytest.raises(IdentityPolicyError, match=">= 2"):
            MatchupConfirmationPolicy(required_observations=1)


class TestAConfirmsRepeatedPair:
    def test_sf_gb_repeated_confirms(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        r1 = v.apply(obs("SF", "GB", index=0, ts=0.0))
        assert r1.outcome is MatchupApplyOutcome.CANDIDATE_STARTED
        assert not r1.state.initialized
        r2 = v.apply(obs("SF", "GB", index=1, ts=2.0))
        assert r2.outcome is MatchupApplyOutcome.CONFIRMED
        assert r2.state.pair == ("SF", "GB")
        assert r2.change is not None and r2.change.initialization
        assert r2.change.evidence_count == 2


class TestBOneFrameNoiseAfterConfirm:
    def test_one_frame_sf_min_does_not_change_confirmed(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        v.apply(obs("SF", "GB", index=1, ts=2.0))
        noise = v.apply(obs("SF", "MIN", index=2, ts=4.0))
        assert noise.outcome is MatchupApplyOutcome.CONFLICT
        assert v.state.pair == ("SF", "GB")
        assert v.state.conflict_count == 1


class TestCUnknownDoesNotFabricate:
    def test_unknown_gb_does_not_invent_sf(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        r = v.apply(obs(None, "GB", index=0, ts=0.0))
        assert r.outcome is MatchupApplyOutcome.IGNORED_PARTIAL
        assert v.candidate is None
        assert v.state.pair is None


class TestDPartialDoesNotConfirm:
    def test_sf_unknown_does_not_confirm(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", None, index=0, ts=0.0))
        v.apply(obs("SF", None, index=1, ts=2.0))
        assert not v.state.initialized
        assert v.candidate is None


class TestENoScatteredVotes:
    def test_alternating_pairs_restart_candidate(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        replaced = v.apply(obs("SF", "MIN", index=1, ts=2.0))
        assert replaced.outcome is MatchupApplyOutcome.CANDIDATE_REPLACED
        assert v.candidate is not None and v.candidate.evidence_count == 1
        v.apply(obs("SF", "GB", index=2, ts=4.0))
        assert not v.state.initialized
        assert v.candidate is not None and v.candidate.pair == ("SF", "GB")
        assert v.candidate.evidence_count == 1


class TestFDuplicate:
    def test_duplicate_frame_adds_no_evidence(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        dup = v.apply(obs("SF", "GB", index=0, ts=0.0))
        assert dup.outcome is MatchupApplyOutcome.IGNORED_DUPLICATE
        assert v.candidate is not None and v.candidate.evidence_count == 1


class TestGStale:
    def test_out_of_order_ignored(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=2, ts=4.0))
        stale = v.apply(obs("SF", "GB", index=1, ts=2.0))
        assert stale.outcome is MatchupApplyOutcome.IGNORED_STALE
        assert v.candidate is not None and v.candidate.evidence_count == 1


class TestHEpochBeforeConfirm:
    def test_source_epoch_change_restarts_candidate(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0, epoch=1))
        reset = v.apply(obs("SF", "GB", index=1, ts=2.0, epoch=2))
        assert reset.outcome is MatchupApplyOutcome.DISCONTINUITY
        assert not v.state.initialized
        assert v.candidate is not None and v.candidate.evidence_count == 1
        v.apply(obs("SF", "GB", index=2, ts=4.0, epoch=2))
        assert v.state.pair == ("SF", "GB")


class TestIEpochAfterConfirm:
    def test_epoch_change_preserves_confirmed(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0, epoch=1))
        v.apply(obs("SF", "GB", index=1, ts=2.0, epoch=1))
        later = v.apply(obs("SF", "GB", index=2, ts=4.0, epoch=2))
        assert later.outcome is MatchupApplyOutcome.MATCHES_CONFIRMED
        assert v.state.pair == ("SF", "GB")


class TestJLongAbsence:
    def test_absence_preserves_confirmed(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        v.apply(obs("SF", "GB", index=1, ts=2.0))
        absent = v.note_absence(frame_index=50, timestamp_s=100.0, source_epoch=1)
        assert absent.outcome is MatchupApplyOutcome.ABSENT
        assert v.state.pair == ("SF", "GB")


class TestKRestartAfterConfirm:
    def test_restore_confirmed_clears_candidate(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        confirmed = v.apply(obs("SF", "GB", index=1, ts=2.0))
        restored = TemporalMatchupValidator(DEFAULT)
        restored.restore(state=confirmed.state, history=v.history)
        assert restored.state.pair == ("SF", "GB")
        assert restored.candidate is None
        assert restored._last_index is None
        again = restored.apply(obs("SF", "GB", index=0, ts=0.0, epoch=9))
        assert again.outcome is MatchupApplyOutcome.MATCHES_CONFIRMED


class TestLRestartCandidateOnly:
    def test_unconfirmed_restart_stays_unconfirmed(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        restored = TemporalMatchupValidator(DEFAULT)
        restored.restore(
            state=ConfirmedMatchupState(
                left_team_id=None,
                right_team_id=None,
                initialized=False,
                revision=0,
            )
        )
        assert restored.candidate is None
        assert not restored.state.initialized


class TestMConflictAfterConfirm:
    def test_repeated_contradiction_does_not_rewrite(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        v.apply(obs("SF", "GB", index=0, ts=0.0))
        v.apply(obs("SF", "GB", index=1, ts=2.0))
        for i in range(3):
            r = v.apply(obs("SF", "MIN", index=2 + i, ts=4.0 + 2 * i))
            assert r.outcome is MatchupApplyOutcome.CONFLICT
            assert r.conflict_pair == ("SF", "MIN")
        assert v.state.pair == ("SF", "GB")
        assert v.state.conflict_count == 3
        assert v.state.last_conflict_pair == ("SF", "MIN")


class TestNSameTeam:
    def test_same_team_both_sides_is_invalid(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        r = v.apply(obs("SF", "SF", index=0, ts=0.0))
        assert r.outcome is MatchupApplyOutcome.IGNORED_INVALID
        v.apply(obs("SF", "SF", index=1, ts=2.0))
        assert not v.state.initialized


class TestRestoreErrors:
    def test_initialized_requires_distinct_teams(self) -> None:
        v = TemporalMatchupValidator(DEFAULT)
        with pytest.raises(IdentityRestoreError):
            v.restore(
                state=ConfirmedMatchupState(
                    left_team_id="SF",
                    right_team_id="SF",
                    initialized=True,
                    revision=1,
                )
            )
