"""Coordinate M7 confirmation with durable PostgreSQL writes.

Write-failure rule: if a confirmation is accepted in memory but the database
write fails, reload the validator from PostgreSQL so in-memory trusted state
cannot stay ahead of durable state.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

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
