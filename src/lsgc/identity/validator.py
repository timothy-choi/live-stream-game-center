"""Temporal confirmation of visual matchup identity.

A raw identity observation never overwrites the confirmed matchup. After
confirmation, a different complete pair is a CONFLICT: the original pair is
kept (one broadcast = one game). Unconfirmed candidates restart across
duplicates, stale frames, epoch changes, and long gaps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from lsgc.identity.errors import IdentityRestoreError
from lsgc.identity.models import (
    CandidateMatchup,
    ConfirmedMatchupChange,
    ConfirmedMatchupState,
    MatchupApplyOutcome,
    MatchupApplyResult,
    MatchupObservation,
)
from lsgc.identity.policy import MatchupConfirmationPolicy


class TemporalMatchupValidator:
    def __init__(self, policy: MatchupConfirmationPolicy | None = None) -> None:
        self.policy = policy or MatchupConfirmationPolicy()
        self._reset_memory()

    def _reset_memory(self) -> None:
        self._state = ConfirmedMatchupState(
            left_team_id=None,
            right_team_id=None,
            initialized=False,
            revision=0,
        )
        self._candidate: CandidateMatchup | None = None
        self._history: list[ConfirmedMatchupChange] = []
        self._last_index: int | None = None
        self._last_ts: float | None = None
        self._last_epoch: int | None = None
        self._frozen = False
        self.conflicts = 0

    @property
    def state(self) -> ConfirmedMatchupState:
        return self._state

    @property
    def candidate(self) -> CandidateMatchup | None:
        return self._candidate

    @property
    def history(self) -> tuple[ConfirmedMatchupChange, ...]:
        return tuple(self._history)

    def restore(
        self,
        *,
        state: ConfirmedMatchupState,
        history: Sequence[ConfirmedMatchupChange] = (),
    ) -> None:
        events = tuple(history)
        if state.initialized:
            if state.pair is None or state.pair[0] == state.pair[1]:
                raise IdentityRestoreError("initialized matchup must be two different teams")
            if state.revision < 1:
                raise IdentityRestoreError("initialized matchup requires revision >= 1")
        elif state.revision != 0 or state.pair is not None:
            raise IdentityRestoreError("uninitialized matchup must have revision 0 and NULL teams")
        self._reset_memory()
        self._state = replace(state)
        self._history = list(events)
        self._candidate = None
        self._last_index = None
        self._last_ts = None
        self._last_epoch = None
        self._frozen = state.frozen
        self.conflicts = state.conflict_count

    def apply(self, observation: MatchupObservation | None) -> MatchupApplyResult:
        if self._frozen:
            return self._result(MatchupApplyOutcome.FROZEN)
        if observation is None:
            return self._result(MatchupApplyOutcome.ABSENT)
        stale = self._stale_or_duplicate(observation)
        if stale is not None:
            return stale
        self._remember(observation)
        if not observation.both_readable:
            self._candidate = None
            reason = (
                MatchupApplyOutcome.IGNORED_PARTIAL
                if observation.left.readable or observation.right.readable
                else MatchupApplyOutcome.IGNORED_UNREADABLE
            )
            return self._result(reason)
        if not observation.is_valid_complete:
            self._candidate = None
            return self._result(MatchupApplyOutcome.IGNORED_INVALID)
        pair = observation.pair
        assert pair is not None
        if self._state.initialized:
            if pair == self._state.pair:
                return self._result(MatchupApplyOutcome.MATCHES_CONFIRMED)
            self.conflicts += 1
            self._state = replace(
                self._state,
                conflict_count=self.conflicts,
                last_conflict_pair=pair,
            )
            self._candidate = None
            return self._result(MatchupApplyOutcome.CONFLICT, conflict_pair=pair)
        return self._accumulate(observation, pair)

    def note_absence(
        self,
        *,
        frame_index: int | None = None,
        timestamp_s: float | None = None,
        source_epoch: int | None = None,
    ) -> MatchupApplyResult:
        if self._frozen:
            return self._result(MatchupApplyOutcome.FROZEN)
        if source_epoch is not None and self._last_epoch is not None:
            if source_epoch > self._last_epoch and not self._state.initialized:
                self._candidate = None
                self._last_epoch = source_epoch
                self._last_index = frame_index
                self._last_ts = timestamp_s
                return self._result(MatchupApplyOutcome.DISCONTINUITY)
        self._last_index = frame_index if frame_index is not None else self._last_index
        self._last_ts = timestamp_s if timestamp_s is not None else self._last_ts
        if source_epoch is not None:
            self._last_epoch = source_epoch
        return self._result(MatchupApplyOutcome.ABSENT)

    def end_acquisition(self) -> MatchupApplyResult:
        self._frozen = True
        self._state = replace(self._state, frozen=True)
        self._candidate = None
        return self._result(MatchupApplyOutcome.FROZEN)

    def _accumulate(
        self, observation: MatchupObservation, pair: tuple[str, str]
    ) -> MatchupApplyResult:
        epoch = observation.source_epoch
        if (
            epoch is not None
            and self._candidate is not None
            and self._candidate.last_epoch is not None
            and epoch > self._candidate.last_epoch
        ):
            self._candidate = None
            started = self._start_candidate(observation, pair)
            return self._result(MatchupApplyOutcome.DISCONTINUITY, candidate=started)
        if self._candidate is not None and self._candidate.pair != pair:
            self._start_candidate(observation, pair)
            return self._result(MatchupApplyOutcome.CANDIDATE_REPLACED)
        if self._candidate is None:
            self._start_candidate(observation, pair)
            return self._result(MatchupApplyOutcome.CANDIDATE_STARTED)
        if self._gap_too_large(observation):
            self._start_candidate(observation, pair)
            return self._result(MatchupApplyOutcome.CANDIDATE_RESET)
        candidate = self._candidate
        evidence = candidate.evidence_count + 1
        self._candidate = replace(
            candidate,
            evidence_count=evidence,
            last_timestamp_s=observation.timestamp_s,
            last_frame_index=observation.frame_index,
            last_epoch=observation.source_epoch,
        )
        if self._ready(self._candidate):
            change = self._confirm(observation, self._candidate)
            return self._result(MatchupApplyOutcome.CONFIRMED, change=change)
        return self._result(MatchupApplyOutcome.CANDIDATE_ACCUMULATED)

    def _start_candidate(
        self, observation: MatchupObservation, pair: tuple[str, str]
    ) -> CandidateMatchup:
        self._candidate = CandidateMatchup(
            left_team_id=pair[0],
            right_team_id=pair[1],
            evidence_count=1,
            first_timestamp_s=observation.timestamp_s,
            first_frame_index=observation.frame_index,
            first_epoch=observation.source_epoch,
            last_timestamp_s=observation.timestamp_s,
            last_frame_index=observation.frame_index,
            last_epoch=observation.source_epoch,
        )
        return self._candidate

    def _ready(self, candidate: CandidateMatchup) -> bool:
        if candidate.evidence_count < self.policy.required_observations:
            return False
        first_ts, last_ts = candidate.first_timestamp_s, candidate.last_timestamp_s
        if first_ts is not None and last_ts is not None:
            return (last_ts - first_ts) + 1e-9 >= self.policy.min_span_s
        first_i, last_i = candidate.first_frame_index, candidate.last_frame_index
        if first_i is not None and last_i is not None:
            return last_i != first_i
        return False

    def _gap_too_large(self, observation: MatchupObservation) -> bool:
        max_gap = self.policy.max_candidate_gap_s
        if max_gap is None or self._candidate is None:
            return False
        prev = self._candidate.last_timestamp_s
        current = observation.timestamp_s
        if prev is None or current is None:
            return False
        return (current - prev) > max_gap

    def _confirm(
        self, observation: MatchupObservation, candidate: CandidateMatchup
    ) -> ConfirmedMatchupChange:
        revision = self._state.revision + 1
        change = ConfirmedMatchupChange(
            revision=revision,
            left_team_id=candidate.left_team_id,
            right_team_id=candidate.right_team_id,
            left_method=observation.left.method,
            right_method=observation.right.method,
            evidence_count=candidate.evidence_count,
            first_seen_timestamp_s=candidate.first_timestamp_s,
            first_seen_frame_index=candidate.first_frame_index,
            confirmed_timestamp_s=observation.timestamp_s,
            confirmed_frame_index=observation.frame_index,
            confirmed_epoch=observation.source_epoch,
            initialization=True,
        )
        self._state = ConfirmedMatchupState(
            left_team_id=candidate.left_team_id,
            right_team_id=candidate.right_team_id,
            initialized=True,
            revision=revision,
            last_confirmed_timestamp_s=observation.timestamp_s,
            last_confirmed_frame_index=observation.frame_index,
            last_confirmed_epoch=observation.source_epoch,
            conflict_count=self.conflicts,
            frozen=self._frozen,
        )
        self._history.append(change)
        self._candidate = None
        return change

    def _stale_or_duplicate(
        self, observation: MatchupObservation
    ) -> MatchupApplyResult | None:
        index = observation.frame_index
        ts = observation.timestamp_s
        epoch = observation.source_epoch
        if index is not None and self._last_index is not None:
            if index < self._last_index:
                return self._result(MatchupApplyOutcome.IGNORED_STALE)
            if index == self._last_index:
                return self._result(MatchupApplyOutcome.IGNORED_DUPLICATE)
        if (
            epoch is not None
            and self._last_epoch is not None
            and ts is not None
            and self._last_ts is not None
            and epoch == self._last_epoch
            and abs(ts - self._last_ts) < 1e-9
        ):
            return self._result(MatchupApplyOutcome.IGNORED_DUPLICATE)
        if epoch is not None and self._last_epoch is not None and epoch < self._last_epoch:
            return self._result(MatchupApplyOutcome.IGNORED_STALE)
        if (
            epoch is not None
            and epoch == self._last_epoch
            and ts is not None
            and self._last_ts is not None
            and ts < self._last_ts
        ):
            return self._result(MatchupApplyOutcome.IGNORED_STALE)
        return None

    def _remember(self, observation: MatchupObservation) -> None:
        if observation.frame_index is not None:
            self._last_index = observation.frame_index
        if observation.timestamp_s is not None:
            self._last_ts = observation.timestamp_s
        if observation.source_epoch is not None:
            self._last_epoch = observation.source_epoch

    def _result(
        self,
        outcome: MatchupApplyOutcome,
        *,
        change: ConfirmedMatchupChange | None = None,
        candidate: CandidateMatchup | None = None,
        conflict_pair: tuple[str, str] | None = None,
    ) -> MatchupApplyResult:
        return MatchupApplyResult(
            outcome=outcome,
            state=self._state,
            candidate=self._candidate if candidate is None else candidate,
            change=change,
            conflict_pair=conflict_pair,
        )
