"""Deterministic temporal validator for trusted live score state.

A raw M6 observation never overwrites trusted state. Confirmation requires
consecutive, in-order, fully-readable observations of the same pair under
`ConfirmationPolicy`. Football scoring rules are not used.

The core is a pure-ish state machine: previous validator + next input ->
`ApplyResult`. It uses observation metadata, never `time.sleep`, wall clock,
YouTube, or a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from lsgc.score.models import RawScoreObservation
from lsgc.track.errors import RestoreError
from lsgc.track.models import (
    ApplyOutcome,
    ApplyResult,
    CandidateScore,
    ConfirmedScoreChange,
    TrustedScoreState,
)
from lsgc.track.policy import ConfirmationPolicy


class TemporalScoreValidator:
    """Consumes a chronological stream of raw observations and absences."""

    def __init__(self, policy: ConfirmationPolicy | None = None) -> None:
        self.policy = policy or ConfirmationPolicy()
        self._reset_memory()

    def _reset_memory(self) -> None:
        self._state = TrustedScoreState(
            left_score=None,
            right_score=None,
            initialized=False,
            revision=0,
        )
        self._candidate: CandidateScore | None = None
        self._history: list[ConfirmedScoreChange] = []
        self._last_index: int | None = None
        self._last_ts: float | None = None
        self._last_epoch: int | None = None
        self._frozen = False
        self.observations_seen = 0
        self.absences_seen = 0
        self.candidate_starts = 0
        self.candidate_resets = 0
        self.candidate_replacements = 0
        self.duplicates_ignored = 0
        self.stale_ignored = 0
        self.initializations = 0
        self.confirmed_changes = 0

    @property
    def state(self) -> TrustedScoreState:
        return self._state

    @property
    def candidate(self) -> CandidateScore | None:
        return self._candidate

    @property
    def history(self) -> tuple[ConfirmedScoreChange, ...]:
        return tuple(self._history)

    def restore(
        self,
        *,
        state: TrustedScoreState,
        history: Sequence[ConfirmedScoreChange] = (),
    ) -> None:
        """Load durable confirmed state after a process restart.

        Restores trusted pair, revision, confirmed history, and freeze flag.
        Does **not** restore candidate evidence, runtime observation-ordering
        cursors (`Frame.index` / PTS / source_epoch), or transient counters
        that exist only for the current acquisition process.

        A new `LiveFrameSession` may restart `Frame.index` at 0. Persisted
        confirmation metadata must not make those observations look stale.
        """
        events = tuple(history)
        self._validate_restore(state, events)
        self._reset_memory()
        self._state = replace(
            state,
            last_observation_timestamp_s=None,
            last_observation_frame_index=None,
            last_observation_epoch=None,
        )
        self._history = list(events)
        self._candidate = None
        self._last_index = None
        self._last_ts = None
        self._last_epoch = None
        self._frozen = bool(state.frozen)
        self.initializations = sum(1 for event in events if event.initialization)
        self.confirmed_changes = sum(1 for event in events if not event.initialization)

    @staticmethod
    def _validate_restore(
        state: TrustedScoreState, events: tuple[ConfirmedScoreChange, ...]
    ) -> None:
        if state.revision < 0:
            raise RestoreError(f"revision must be >= 0, got {state.revision}")
        if state.revision == 0:
            if state.initialized or state.left_score is not None or state.right_score is not None:
                raise RestoreError("revision 0 must be uninitialized with NULL scores")
            if events:
                raise RestoreError("revision 0 cannot have confirmed history")
            return
        if not state.initialized:
            raise RestoreError("revision > 0 must be initialized")
        if state.left_score is None or state.right_score is None:
            raise RestoreError("initialized state must have both scores")
        if not events:
            raise RestoreError("initialized state requires confirmed history")
        revisions = [event.revision for event in events]
        expected = list(range(1, len(events) + 1))
        if revisions != expected:
            raise RestoreError(f"history revisions must be 1..n consecutive, got {revisions}")
        last = events[-1]
        if last.revision != state.revision:
            raise RestoreError(
                f"state revision {state.revision} != last event revision {last.revision}"
            )
        if last.new_pair != (state.left_score, state.right_score):
            raise RestoreError("current trusted pair must equal the newest confirmed event")
        for index, event in enumerate(events):
            if index == 0:
                if not event.initialization or event.previous_pair is not None:
                    raise RestoreError("revision 1 must be initialization with previous=NULL")
            else:
                if event.initialization:
                    raise RestoreError("only revision 1 may be an initialization")
                previous = events[index - 1]
                if event.previous_pair != previous.new_pair:
                    raise RestoreError("event previous pair must equal prior event new pair")
            if event.evidence_count < 2:
                raise RestoreError("confirmed events require evidence_count >= 2")

    def reset(self) -> ApplyResult:
        """Explicit new-game / new tracking context. Not inferred from video."""
        self._reset_memory()
        return ApplyResult(
            outcome=ApplyOutcome.RESET,
            state=self._state,
            candidate=None,
        )

    def end_acquisition(self) -> ApplyResult:
        """Acquisition ended. Preserve trusted score. This is not a game-final."""
        self._frozen = True
        self._state = replace(self._state, frozen=True)
        return ApplyResult(
            outcome=ApplyOutcome.FROZEN,
            state=self._state,
            candidate=self._candidate,
        )

    def note_absence(
        self,
        *,
        frame_index: int | None = None,
        timestamp_s: float | None = None,
        source_epoch: int | None = None,
    ) -> ApplyResult:
        """Scoreboard ABSENT / no M6 observation. Trusted score is unchanged."""
        if self._frozen:
            return ApplyResult(ApplyOutcome.FROZEN, self._state, self._candidate)
        self.absences_seen += 1
        order = self._order(frame_index, timestamp_s, source_epoch)
        if order in (ApplyOutcome.IGNORED_DUPLICATE, ApplyOutcome.IGNORED_STALE):
            return self._ignore(order)
        if order == ApplyOutcome.DISCONTINUITY:
            self._clear_candidate(reset=True)
        elif self._gap_too_large(timestamp_s, source_epoch, self._candidate):
            self._clear_candidate(reset=True)
        self._remember(frame_index, timestamp_s, source_epoch)
        return ApplyResult(ApplyOutcome.ABSENT, self._state, self._candidate)

    def apply(self, observation: RawScoreObservation | None) -> ApplyResult:
        """Apply one raw observation, or None for an absent/missing sample."""
        if observation is None:
            return self.note_absence()
        if self._frozen:
            return ApplyResult(ApplyOutcome.FROZEN, self._state, self._candidate)

        self.observations_seen += 1
        order = self._order(
            observation.frame_index, observation.timestamp_s, observation.source_epoch
        )
        if order in (ApplyOutcome.IGNORED_DUPLICATE, ApplyOutcome.IGNORED_STALE):
            return self._ignore(order)

        if order == ApplyOutcome.DISCONTINUITY:
            self._clear_candidate(reset=True)

        self._remember(
            observation.frame_index, observation.timestamp_s, observation.source_epoch
        )
        self._state = replace(
            self._state,
            last_observation_timestamp_s=observation.timestamp_s,
            last_observation_frame_index=observation.frame_index,
            last_observation_epoch=observation.source_epoch,
        )

        if not observation.left.readable and not observation.right.readable:
            if self._gap_too_large(
                observation.timestamp_s, observation.source_epoch, self._candidate
            ):
                self._clear_candidate(reset=True)
            return ApplyResult(
                ApplyOutcome.IGNORED_UNREADABLE, self._state, self._candidate
            )

        if not observation.both_readable:
            # Partial: never invent the missing side, never confirm a pair.
            if self._gap_too_large(
                observation.timestamp_s, observation.source_epoch, self._candidate
            ):
                self._clear_candidate(reset=True)
            return ApplyResult(
                ApplyOutcome.IGNORED_PARTIAL, self._state, self._candidate
            )

        pair = (observation.left.value, observation.right.value)
        assert pair[0] is not None and pair[1] is not None

        if self._state.initialized and self._state.pair == pair:
            if self._candidate is not None:
                self._clear_candidate(reset=True)
                return ApplyResult(
                    ApplyOutcome.CANDIDATE_RESET, self._state, None
                )
            return ApplyResult(ApplyOutcome.MATCHES_TRUSTED, self._state, None)

        return self._consider_candidate(observation, pair)

    def _consider_candidate(
        self, observation: RawScoreObservation, pair: tuple[int, int]
    ) -> ApplyResult:
        left, right = pair
        if self._candidate is not None and self._candidate.pair != pair:
            self.candidate_replacements += 1
            self._candidate = self._new_candidate(observation, left, right)
            return ApplyResult(
                ApplyOutcome.CANDIDATE_REPLACED, self._state, self._candidate
            )

        if self._candidate is None:
            self.candidate_starts += 1
            self._candidate = self._new_candidate(observation, left, right)
            if self._ready(self._candidate):
                return self._confirm(observation)
            return ApplyResult(
                ApplyOutcome.CANDIDATE_STARTED, self._state, self._candidate
            )

        if self._gap_too_large(
            observation.timestamp_s, observation.source_epoch, self._candidate
        ):
            self.candidate_starts += 1
            self._candidate = self._new_candidate(observation, left, right)
            if self._ready(self._candidate):
                return self._confirm(observation)
            return ApplyResult(
                ApplyOutcome.CANDIDATE_STARTED, self._state, self._candidate
            )

        self._candidate = replace(
            self._candidate,
            evidence_count=self._candidate.evidence_count + 1,
            last_timestamp_s=observation.timestamp_s,
            last_frame_index=observation.frame_index,
            last_epoch=observation.source_epoch,
        )
        if self._ready(self._candidate):
            return self._confirm(observation)
        return ApplyResult(
            ApplyOutcome.CANDIDATE_ACCUMULATED, self._state, self._candidate
        )

    def _new_candidate(
        self, observation: RawScoreObservation, left: int, right: int
    ) -> CandidateScore:
        return CandidateScore(
            left=left,
            right=right,
            evidence_count=1,
            first_timestamp_s=observation.timestamp_s,
            first_frame_index=observation.frame_index,
            first_epoch=observation.source_epoch,
            last_timestamp_s=observation.timestamp_s,
            last_frame_index=observation.frame_index,
            last_epoch=observation.source_epoch,
        )

    def _ready(self, candidate: CandidateScore) -> bool:
        if candidate.evidence_count < self.policy.required_observations:
            return False
        first_ts, last_ts = candidate.first_timestamp_s, candidate.last_timestamp_s
        if first_ts is not None and last_ts is not None:
            return (last_ts - first_ts) >= self.policy.min_span_s
        first_i, last_i = candidate.first_frame_index, candidate.last_frame_index
        if first_i is not None and last_i is not None:
            return (last_i - first_i) >= (self.policy.required_observations - 1)
        # No time and no indices: distinct applications still need the count,
        # but cannot prove a span. Refuse rather than confirm on stacked clones.
        return False

    def _confirm(self, observation: RawScoreObservation) -> ApplyResult:
        candidate = self._candidate
        assert candidate is not None
        previous_left = self._state.left_score
        previous_right = self._state.right_score
        initialization = not self._state.initialized
        revision = self._state.revision + 1
        change = ConfirmedScoreChange(
            revision=revision,
            previous_left=previous_left,
            previous_right=previous_right,
            new_left=candidate.left,
            new_right=candidate.right,
            evidence_count=candidate.evidence_count,
            first_seen_timestamp_s=candidate.first_timestamp_s,
            first_seen_frame_index=candidate.first_frame_index,
            confirmed_timestamp_s=observation.timestamp_s,
            confirmed_frame_index=observation.frame_index,
            confirmed_epoch=observation.source_epoch,
            initialization=initialization,
        )
        self._history.append(change)
        self._state = replace(
            self._state,
            left_score=candidate.left,
            right_score=candidate.right,
            initialized=True,
            revision=revision,
            last_confirmed_timestamp_s=observation.timestamp_s,
            last_confirmed_frame_index=observation.frame_index,
            last_confirmed_epoch=observation.source_epoch,
        )
        self._candidate = None
        if initialization:
            self.initializations += 1
        else:
            self.confirmed_changes += 1
        return ApplyResult(ApplyOutcome.CONFIRMED, self._state, None, change)

    def _clear_candidate(self, *, reset: bool) -> None:
        if self._candidate is not None and reset:
            self.candidate_resets += 1
        self._candidate = None

    def _gap_too_large(
        self,
        timestamp_s: float | None,
        epoch: int | None,
        candidate: CandidateScore | None,
    ) -> bool:
        limit = self.policy.max_candidate_gap_s
        if limit is None or candidate is None:
            return False
        if timestamp_s is None or candidate.last_timestamp_s is None:
            return False
        if epoch is not None and candidate.last_epoch is not None and epoch != candidate.last_epoch:
            return True
        return (timestamp_s - candidate.last_timestamp_s) > limit

    def _order(
        self,
        frame_index: int | None,
        timestamp_s: float | None,
        source_epoch: int | None,
    ) -> ApplyOutcome | None:
        same_index = (
            frame_index is not None
            and self._last_index is not None
            and frame_index == self._last_index
        )
        same_epoch_pts = (
            source_epoch is not None
            and self._last_epoch is not None
            and timestamp_s is not None
            and self._last_ts is not None
            and source_epoch == self._last_epoch
            and timestamp_s == self._last_ts
        )
        if same_index or same_epoch_pts:
            return ApplyOutcome.IGNORED_DUPLICATE

        if (
            frame_index is not None
            and self._last_index is not None
            and frame_index < self._last_index
        ):
            return ApplyOutcome.IGNORED_STALE

        if source_epoch is not None and self._last_epoch is not None:
            if source_epoch < self._last_epoch:
                return ApplyOutcome.IGNORED_STALE
            if source_epoch > self._last_epoch:
                return ApplyOutcome.DISCONTINUITY
            if timestamp_s is not None and self._last_ts is not None:
                if timestamp_s < self._last_ts:
                    return ApplyOutcome.IGNORED_STALE
                if self.policy.max_candidate_gap_s is not None:
                    if (timestamp_s - self._last_ts) > self.policy.max_candidate_gap_s:
                        return ApplyOutcome.DISCONTINUITY

        if (
            source_epoch is None
            and timestamp_s is not None
            and self._last_ts is not None
            and timestamp_s < self._last_ts
        ):
            return ApplyOutcome.IGNORED_STALE
        return None

    def _remember(
        self,
        frame_index: int | None,
        timestamp_s: float | None,
        source_epoch: int | None,
    ) -> None:
        if frame_index is not None:
            self._last_index = frame_index
        if timestamp_s is not None:
            self._last_ts = timestamp_s
        if source_epoch is not None:
            self._last_epoch = source_epoch

    def _ignore(self, outcome: ApplyOutcome) -> ApplyResult:
        if outcome is ApplyOutcome.IGNORED_DUPLICATE:
            self.duplicates_ignored += 1
        elif outcome is ApplyOutcome.IGNORED_STALE:
            self.stale_ignored += 1
        return ApplyResult(outcome, self._state, self._candidate)
