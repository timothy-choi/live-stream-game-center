"""Coordinate M7 confirmation with durable PostgreSQL writes.

Write-failure rule: if a confirmation is accepted in memory but the database
write fails, reload the validator from PostgreSQL so in-memory trusted state
cannot stay ahead of durable state.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from lsgc.identity.policy import MatchupConfirmationPolicy
from lsgc.identity.validator import TemporalMatchupValidator
from lsgc.identity.policy import MatchupConfirmationPolicy
from lsgc.identity.validator import TemporalMatchupValidator
from lsgc.persist.models import PLATFORM_YOUTUBE, Game
from lsgc.score.models import RawScoreObservation
from lsgc.track.models import ApplyResult, ConfirmedScoreChange
from lsgc.track.policy import ConfirmationPolicy
from lsgc.track.validator import TemporalScoreValidator


class DurableScoreTracker:
    """M7 validator + repository transaction boundary.

    Does not contain SQL. Does not infer a new game from 0-0 or reconnect.
    M7 `reset()` is not mapped to DELETE of persisted history.
    """

    def __init__(
        self,
        store: object,
        validator: TemporalScoreValidator,
        game_id: UUID,
    ) -> None:
        self._store = store
        self.validator = validator
        self._game_id = game_id

    @classmethod
    def open(
        cls,
        store: object,
        *,
        platform: str = PLATFORM_YOUTUBE,
        external_broadcast_id: str,
        channel_id: str | None = None,
        policy: ConfirmationPolicy | None = None,
    ) -> DurableScoreTracker:
        game = store.create_or_get_game(  # type: ignore[attr-defined]
            platform=platform,
            external_broadcast_id=external_broadcast_id,
            channel_id=channel_id,
        )
        history = store.list_confirmed_changes(game.id)  # type: ignore[attr-defined]
        validator = TemporalScoreValidator(policy)
        validator.restore(state=game.trusted_state(), history=history)
        return cls(store, validator, game.id)

    @property
    def game_id(self) -> UUID:
        return self._game_id

    @property
    def game(self) -> Game:
        return self._store.get_game(self._game_id)  # type: ignore[attr-defined]

    def apply(self, observation: RawScoreObservation | None) -> ApplyResult:
        result = self.validator.apply(observation)
        if result.change is not None:
            self._persist_change(result.change)
        return result

    def note_absence(
        self,
        *,
        frame_index: int | None = None,
        timestamp_s: float | None = None,
        source_epoch: int | None = None,
    ) -> ApplyResult:
        return self.validator.note_absence(
            frame_index=frame_index,
            timestamp_s=timestamp_s,
            source_epoch=source_epoch,
        )

    def end_acquisition(self) -> ApplyResult:
        result = self.validator.end_acquisition()
        try:
            self._store.mark_acquisition_ended(self._game_id)  # type: ignore[attr-defined]
        except Exception:
            self._reload()
            raise
        return result

    def _persist_change(self, change: ConfirmedScoreChange) -> None:
        try:
            self._store.persist_confirmation(self._game_id, change)  # type: ignore[attr-defined]
        except Exception:
            self._reload()
            raise

    def _reload(self) -> None:
        game = self._store.get_game(self._game_id)  # type: ignore[attr-defined]
        history: Sequence[ConfirmedScoreChange] = self._store.list_confirmed_changes(  # type: ignore[attr-defined]
            self._game_id
        )
        self.validator.restore(state=game.trusted_state(), history=history)


class DurableMatchupTracker:
    """Temporal matchup validator + repository transaction boundary.

    Write-failure rule: if confirmation is accepted in memory but the database
    write fails, reload from PostgreSQL so memory cannot stay ahead of durable
    confirmed matchup. Candidate evidence is never persisted.
    """

    def __init__(
        self,
        store: object,
        validator: TemporalMatchupValidator,
        game_id: UUID,
    ) -> None:
        self._store = store
        self.validator = validator
        self._game_id = game_id

    @classmethod
    def open(
        cls,
        store: object,
        *,
        platform: str = PLATFORM_YOUTUBE,
        external_broadcast_id: str,
        channel_id: str | None = None,
        policy: MatchupConfirmationPolicy | None = None,
    ) -> DurableMatchupTracker:
        game = store.create_or_get_game(  # type: ignore[attr-defined]
            platform=platform,
            external_broadcast_id=external_broadcast_id,
            channel_id=channel_id,
        )
        history = store.list_matchup_changes(game.id)  # type: ignore[attr-defined]
        validator = TemporalMatchupValidator(policy or MatchupConfirmationPolicy())
        validator.restore(state=game.matchup_state(), history=history)
        return cls(store, validator, game.id)

    @property
    def game_id(self) -> UUID:
        return self._game_id

    @property
    def game(self) -> Game:
        return self._store.get_game(self._game_id)  # type: ignore[attr-defined]

    @property
    def title_disagreement(self) -> str | None:
        from lsgc.identity.catalog import title_visual_disagreement

        game = self.game
        pair = game.matchup_state().pair
        if pair is None:
            return None
        return title_visual_disagreement(game.title, pair[0], pair[1])

    def apply(self, observation) -> object:
        from lsgc.identity.models import MatchupApplyOutcome

        result = self.validator.apply(observation)
        if result.change is not None:
            self._persist_change(result.change)
        elif result.outcome is MatchupApplyOutcome.CONFLICT and result.conflict_pair:
            self._persist_conflict(result.conflict_pair)
        return result

    def note_absence(
        self,
        *,
        frame_index: int | None = None,
        timestamp_s: float | None = None,
        source_epoch: int | None = None,
    ):
        return self.validator.note_absence(
            frame_index=frame_index,
            timestamp_s=timestamp_s,
            source_epoch=source_epoch,
        )

    def end_acquisition(self):
        return self.validator.end_acquisition()

    def _persist_change(self, change) -> None:
        try:
            self._store.persist_matchup_confirmation(self._game_id, change)  # type: ignore[attr-defined]
        except Exception:
            self._reload()
            raise

    def _persist_conflict(self, conflict_pair: tuple[str, str]) -> None:
        try:
            self._store.record_matchup_conflict(self._game_id, conflict_pair)  # type: ignore[attr-defined]
        except Exception:
            self._reload()
            raise

    def _reload(self) -> None:
        game = self._store.get_game(self._game_id)  # type: ignore[attr-defined]
        history = self._store.list_matchup_changes(self._game_id)  # type: ignore[attr-defined]
        self.validator.restore(state=game.matchup_state(), history=history)
