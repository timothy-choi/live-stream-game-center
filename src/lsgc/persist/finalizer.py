"""Coordinate completion detection with durable FINALIZING / FINAL writes.

YouTube ENDED is not consulted here. FINAL is written only after:
confirmed completion, confirmed postgame score, successful reconciliation,
persisted durable score equality, and the stability interval.

When the confirmed postgame pair differs from the latest durable gameplay
score, missed scoreboard states are recovered from available video. The
numeric difference is not expanded into sport-specific scoring events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from lsgc.finalize.policy import FinalizationPolicy
from lsgc.finalize.reconcile import ReconciliationKind, reconcile_final_score
from lsgc.finalize.recovery import RecoveryCoverage, RecoverySource
from lsgc.finalize.scanner import recovered_change, scan_recovered_scores
from lsgc.finalize.temporal import CompletionConfirmer, CompletionEvidence, PostgameScoreConfirmer
from lsgc.persist.errors import SchemaError
from lsgc.persist.models import CompletionStatus, Game, HistoryCompleteness, RecoveryStatus
from lsgc.score.models import RawScoreObservation, ScoreRead
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance
from lsgc.track.policy import ConfirmationPolicy
from lsgc.vision.detector import DetectionResult

_SETTLED_RECOVERY = {
    RecoveryStatus.NOT_NEEDED,
    RecoveryStatus.COMPLETE,
    RecoveryStatus.PARTIAL,
    RecoveryStatus.UNAVAILABLE,
}


class DurableFinalizationTracker:
    def __init__(
        self,
        store: object,
        game_id: UUID,
        *,
        policy: FinalizationPolicy | None = None,
        recovery_source: RecoverySource | None = None,
        gameplay_pipeline: ScoreObservationPipeline | None = None,
    ) -> None:
        self._store = store
        self._game_id = game_id
        self.policy = policy or FinalizationPolicy()
        self._recovery_source = recovery_source
        self._gameplay_pipeline = gameplay_pipeline
        self._completion = CompletionConfirmer(self.policy)
        self._postgame = PostgameScoreConfirmer(self.policy)
        game = self.game
        if game.completion_status in {CompletionStatus.FINALIZING, CompletionStatus.FINAL}:
            self._completion.restore_confirmed()

    @classmethod
    def open(
        cls,
        store: object,
        game_id: UUID,
        *,
        policy: FinalizationPolicy | None = None,
        recovery_source: RecoverySource | None = None,
        gameplay_pipeline: ScoreObservationPipeline | None = None,
    ) -> DurableFinalizationTracker:
        return cls(
            store,
            game_id,
            policy=policy,
            recovery_source=recovery_source,
            gameplay_pipeline=gameplay_pipeline,
        )

    @property
    def game(self) -> Game:
        return self._store.get_game(self._game_id)  # type: ignore[attr-defined]

    def observe_presence(
        self,
        present: bool,
        *,
        timestamp_s: float | None,
        frame_index: int | None,
        now: datetime | None = None,
    ) -> Game:
        game = self.game
        if game.completion_status is CompletionStatus.FINAL:
            return game
        evidence = self._completion.observe(
            present, timestamp_s=timestamp_s, frame_index=frame_index
        )
        if evidence is not CompletionEvidence.COMPLETION_CONFIRMED:
            return game
        if game.completion_status is CompletionStatus.FINALIZING:
            return game
        started = now or datetime.now(tz=UTC)
        deadline = started + timedelta(seconds=self.policy.drain_s)
        return self._store.enter_finalizing(  # type: ignore[attr-defined]
            self._game_id,
            confirmed_timestamp_s=timestamp_s,
            confirmed_frame_index=frame_index,
            deadline_at=deadline,
            started_at=started,
        )

    def observe_postgame_score(
        self,
        observation: RawScoreObservation,
    ) -> Game:
        game = self.game
        if game.completion_status is not CompletionStatus.FINALIZING:
            return game
        change = self._postgame.confirmed_change(observation)
        if change is None:
            return game
        pair = change.new_pair
        decision = reconcile_final_score(game.pair, pair)
        if decision.kind is ReconciliationKind.CONFLICT:
            self._record_recovery(RecoveryStatus.CONFLICT, HistoryCompleteness.UNKNOWN)
            return self._record_postgame(
                pair,
                observation.timestamp_s,
                conflict=decision.detail,
                reset_stability=True,
            )
        if decision.kind is ReconciliationKind.AGREE:
            if game.recovery_status not in _SETTLED_RECOVERY:
                self._record_recovery(RecoveryStatus.NOT_NEEDED, HistoryCompleteness.COMPLETE)
            return self._record_postgame(
                pair,
                observation.timestamp_s,
                conflict=None,
                reset_stability=False,
            )
        return self._recover_then_reconcile(game, pair, observation)

    def _recover_then_reconcile(
        self,
        game: Game,
        postgame: tuple[int, int],
        observation: RawScoreObservation,
    ) -> Game:
        if (
            game.recovery_status in _SETTLED_RECOVERY
            and game.postgame_pair == postgame
            and game.pair == postgame
            and not game.final_score_conflict
        ):
            return self._record_postgame(
                postgame,
                observation.timestamp_s,
                conflict=None,
                reset_stability=False,
            )
        self._record_recovery(RecoveryStatus.RECOVERING, None)
        scan = self._scan_recovery(game, end_s=observation.timestamp_s)
        if scan.conflict:
            self._record_recovery(RecoveryStatus.CONFLICT, HistoryCompleteness.UNKNOWN)
            return self._record_postgame(
                postgame,
                observation.timestamp_s,
                conflict=scan.detail,
                reset_stability=True,
            )
        for transition in scan.transitions:
            current = self.game
            if current.pair == transition.pair:
                continue
            if current.pair is not None and (
                transition.pair[0] < current.pair[0] or transition.pair[1] < current.pair[1]
            ):
                detail = (
                    f"recovered pair {transition.pair[0]}-{transition.pair[1]} decreases "
                    f"terminal {current.pair[0]}-{current.pair[1]}"
                )
                self._record_recovery(RecoveryStatus.CONFLICT, HistoryCompleteness.UNKNOWN)
                return self._record_postgame(
                    postgame,
                    observation.timestamp_s,
                    conflict=detail,
                    reset_stability=True,
                )
            persist = recovered_change(
                current.score_revision + 1,
                transition,
                initialization=current.score_revision == 0,
            )
            self._store.persist_confirmation(self._game_id, persist)  # type: ignore[attr-defined]

        current = self.game
        recovered_terminal = current.pair
        if recovered_terminal is not None and (
            recovered_terminal[0] > postgame[0] or recovered_terminal[1] > postgame[1]
        ):
            detail = (
                f"recovered terminal {recovered_terminal[0]}-{recovered_terminal[1]} "
                f"contradicts postgame {postgame[0]}-{postgame[1]}"
            )
            self._record_recovery(RecoveryStatus.CONFLICT, HistoryCompleteness.UNKNOWN)
            return self._record_postgame(
                postgame,
                observation.timestamp_s,
                conflict=detail,
                reset_stability=True,
            )
        if recovered_terminal == postgame:
            completeness = (
                HistoryCompleteness.COMPLETE
                if scan.coverage is RecoveryCoverage.FULL
                else HistoryCompleteness.PARTIAL
            )
            status = (
                RecoveryStatus.COMPLETE
                if completeness is HistoryCompleteness.COMPLETE
                else RecoveryStatus.PARTIAL
            )
            self._record_recovery(status, completeness)
            return self._record_postgame(
                postgame,
                observation.timestamp_s,
                conflict=None,
                reset_stability=True,
            )
        self._persist_postgame_terminal(current, postgame, observation)
        completeness = (
            HistoryCompleteness.UNAVAILABLE
            if scan.coverage is RecoveryCoverage.NONE
            else HistoryCompleteness.PARTIAL
        )
        status = (
            RecoveryStatus.UNAVAILABLE
            if completeness is HistoryCompleteness.UNAVAILABLE
            else RecoveryStatus.PARTIAL
        )
        self._record_recovery(status, completeness)
        return self._record_postgame(
            postgame,
            observation.timestamp_s,
            conflict=None,
            reset_stability=True,
        )

    def _scan_recovery(self, game: Game, *, end_s: float | None):
        from lsgc.finalize.scanner import RecoveryScanResult

        if self._recovery_source is None or self._gameplay_pipeline is None:
            return RecoveryScanResult(
                transitions=(),
                coverage=RecoveryCoverage.NONE,
                conflict=False,
                detail="recovery source or gameplay pipeline unavailable",
            )
        window = self._recovery_source.scan(
            start_s=game.last_confirmed_timestamp_s,
            end_s=end_s,
            interval_s=self.policy.recovery_scan_interval_s,
        )
        if not window.frames:
            return RecoveryScanResult(
                transitions=(),
                coverage=window.coverage,
                conflict=False,
                detail="no recoverable video in the configured window",
            )
        history = self._store.list_confirmed_changes(self._game_id)  # type: ignore[attr-defined]
        known = {item.new_pair for item in history}
        policy = ConfirmationPolicy(
            required_observations=self.policy.recovery_required_observations,
            min_span_s=self.policy.recovery_min_span_s,
        )
        return scan_recovered_scores(
            window,
            self._gameplay_pipeline,
            durable_pair=game.pair,
            known_pairs=known,
            policy=policy,
        )

    def _persist_postgame_terminal(
        self,
        game: Game,
        postgame: tuple[int, int],
        observation: RawScoreObservation,
    ) -> None:
        if game.pair == postgame:
            return
        persist_change = ConfirmedScoreChange(
            revision=game.score_revision + 1,
            previous_left=game.trusted_left_score,
            previous_right=game.trusted_right_score,
            new_left=postgame[0],
            new_right=postgame[1],
            evidence_count=2,
            first_seen_timestamp_s=observation.timestamp_s,
            first_seen_frame_index=observation.frame_index,
            confirmed_timestamp_s=observation.timestamp_s,
            confirmed_frame_index=observation.frame_index,
            confirmed_epoch=observation.source_epoch,
            initialization=game.score_revision == 0,
            source=ScoreProvenance.POSTGAME_FINAL,
        )
        self._store.persist_confirmation(self._game_id, persist_change)  # type: ignore[attr-defined]

    def _record_postgame(
        self,
        pair: tuple[int, int],
        timestamp_s: float | None,
        *,
        conflict: str | None,
        reset_stability: bool,
    ) -> Game:
        return self._store.record_postgame_score(  # type: ignore[attr-defined]
            self._game_id,
            left=pair[0],
            right=pair[1],
            confirmed_timestamp_s=timestamp_s,
            conflict=conflict,
            reset_stability=reset_stability,
            stable_timestamp_s=timestamp_s,
        )

    def _record_recovery(
        self,
        status: RecoveryStatus,
        completeness: HistoryCompleteness | None,
    ) -> Game:
        return self._store.record_recovery_status(  # type: ignore[attr-defined]
            self._game_id,
            recovery_status=status,
            score_history_completeness=completeness,
        )

    def maybe_finalize(self, *, timestamp_s: float | None) -> Game:
        game = self.game
        if game.completion_status is CompletionStatus.FINAL:
            return game
        if game.completion_status is not CompletionStatus.FINALIZING:
            return game
        if game.final_score_conflict:
            return game
        if game.recovery_status is RecoveryStatus.CONFLICT:
            return game
        if game.recovery_status is RecoveryStatus.RECOVERING:
            return game
        if game.postgame_pair is None or game.pair is None:
            return game
        if game.pair != game.postgame_pair:
            return game
        stable_ts = game.score_stable_since_timestamp_s
        if timestamp_s is not None and stable_ts is not None:
            elapsed = timestamp_s - stable_ts
        elif game.score_stable_since_at is not None:
            elapsed = (datetime.now(tz=UTC) - game.score_stable_since_at).total_seconds()
        else:
            return game
        if elapsed < self.policy.stability_s:
            return game
        try:
            return self._store.mark_final(self._game_id)  # type: ignore[attr-defined]
        except SchemaError:
            return self.game


def postgame_observation(
    *,
    left: ScoreRead,
    right: ScoreRead,
    detection: DetectionResult,
    frame_index: int | None,
    timestamp_s: float | None,
    source_epoch: int | None = None,
) -> RawScoreObservation:
    return RawScoreObservation(
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        received_at=datetime.now(tz=UTC),
        left=left,
        right=right,
        detection_present=detection.present,
        detection_score=detection.score,
        detection_threshold=detection.threshold,
        channel_id=detection.channel_id,
        profile_id=detection.profile_id,
        profile_revision=detection.profile_revision,
        frame_width=detection.frame_width,
        frame_height=detection.frame_height,
        source_epoch=source_epoch,
    )
