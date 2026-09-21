"""In-memory GameRepository stand-in for offline DurableScoreTracker tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from lsgc.persist.errors import (
    ConflictingConfirmationError,
    ConflictingMatchupError,
    PersistError,
    SchemaError,
    StaleConfirmationError,
    UnknownGameError,
)
from lsgc.persist.models import (
    Game,
    MatchupPersistResult,
    PersistOutcome,
    PersistResult,
    PlatformLifecycle,
    TrackingStatus,
)
from lsgc.persist.repository import (
    event_payload_matches,
    matchup_payload_matches,
    validate_broadcast_id,
)
from lsgc.identity.models import ConfirmedMatchupChange
from lsgc.track.models import ConfirmedScoreChange


class InMemoryGameRepository:
    def __init__(self) -> None:
        self._games: dict[UUID, Game] = {}
        self._by_identity: dict[tuple[str, str], UUID] = {}
        self._history: dict[UUID, list[ConfirmedScoreChange]] = {}
        self._matchup_history: dict[UUID, list[ConfirmedMatchupChange]] = {}
        self.fail_next_persist = False
        self.fail_next_end = False
        self.fail_next_matchup_persist = False
        self.fail_next_matchup_conflict = False

    def create_or_get_game(
        self,
        *,
        platform: str,
        external_broadcast_id: str,
        channel_id: str | None = None,
    ) -> Game:
        validate_broadcast_id(platform, external_broadcast_id)
        identity = (platform, external_broadcast_id.strip())
        existing = self._by_identity.get(identity)
        now = datetime.now(tz=UTC)
        if existing is not None:
            game = self._games[existing]
            if channel_id and game.channel_id is None:
                game = self._replace(game, channel_id=channel_id, updated_at=now)
                self._games[game.id] = game
            return game
        game = Game(
            id=uuid4(),
            platform=platform,
            external_broadcast_id=identity[1],
            channel_id=channel_id,
            tracking_status=TrackingStatus.TRACKING,
            trusted_left_score=None,
            trusted_right_score=None,
            score_revision=0,
            last_confirmed_at=None,
            last_confirmed_timestamp_s=None,
            last_confirmed_frame_index=None,
            last_confirmed_epoch=None,
            created_at=now,
            updated_at=now,
        )
        self._games[game.id] = game
        self._by_identity[identity] = game.id
        self._history[game.id] = []
        self._matchup_history[game.id] = []
        return game

    def get_game(self, game_id: UUID) -> Game:
        try:
            return self._games[game_id]
        except KeyError as exc:
            raise UnknownGameError(f"game {game_id} not found") from exc

    def get_game_by_external_id(
        self, platform: str, external_broadcast_id: str
    ) -> Game | None:
        game_id = self._by_identity.get((platform, external_broadcast_id.strip()))
        return None if game_id is None else self._games[game_id]

    def list_games_for_channel(self, channel_id: str) -> tuple[Game, ...]:
        games = [g for g in self._games.values() if g.channel_id == channel_id]
        games.sort(
            key=lambda g: (
                g.scheduled_start_at is None,
                g.scheduled_start_at or g.created_at,
                g.created_at,
                str(g.id),
            )
        )
        return tuple(games)

    def upsert_broadcast_metadata(
        self,
        *,
        platform: str,
        external_broadcast_id: str,
        channel_id: str,
        title: str | None,
        platform_lifecycle: PlatformLifecycle,
        scheduled_start_at: datetime | None,
        actual_start_at: datetime | None,
        actual_end_at: datetime | None,
        synced_at: datetime,
    ) -> Game:
        game = self.create_or_get_game(
            platform=platform,
            external_broadcast_id=external_broadcast_id,
            channel_id=channel_id,
        )
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            title=title if title is not None else game.title,
            platform_lifecycle=platform_lifecycle,
            scheduled_start_at=(
                scheduled_start_at
                if scheduled_start_at is not None
                else game.scheduled_start_at
            ),
            actual_start_at=(
                actual_start_at if actual_start_at is not None else game.actual_start_at
            ),
            actual_end_at=(
                actual_end_at if actual_end_at is not None else game.actual_end_at
            ),
            last_synced_at=synced_at,
            channel_id=game.channel_id or channel_id,
            updated_at=now,
        )
        self._games[game.id] = updated
        return updated

    def set_tracking_enabled(self, game_id: UUID, enabled: bool) -> Game:
        game = self.get_game(game_id)
        updated = self._replace(
            game,
            tracking_enabled=enabled,
            updated_at=datetime.now(tz=UTC),
        )
        self._games[game_id] = updated
        return updated

    def record_worker_finished(
        self,
        game_id: UUID,
        *,
        error: str | None,
        finished_at: datetime | None = None,
    ) -> Game:
        game = self.get_game(game_id)
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            last_tracking_error=error,
            last_worker_finished_at=finished_at or now,
            updated_at=now,
        )
        self._games[game_id] = updated
        return updated

    def list_confirmed_changes(self, game_id: UUID) -> tuple[ConfirmedScoreChange, ...]:
        self.get_game(game_id)
        return tuple(self._history[game_id])

    def persist_confirmation(
        self, game_id: UUID, change: ConfirmedScoreChange
    ) -> PersistResult:
        if self.fail_next_persist:
            self.fail_next_persist = False
            raise PersistError("injected write failure")
        game = self.get_game(game_id)
        history = list(self._history[game_id])
        if change.revision == game.score_revision:
            stored = next((e for e in history if e.revision == change.revision), None)
            if stored is not None and event_payload_matches(stored, change):
                return PersistResult(PersistOutcome.IDEMPOTENT, game, stored)
            raise ConflictingConfirmationError(
                f"revision {change.revision} already persisted with different data"
            )
        if change.revision < game.score_revision:
            raise StaleConfirmationError(
                f"revision {change.revision} is older than persisted {game.score_revision}"
            )
        if change.revision != game.score_revision + 1:
            raise ConflictingConfirmationError(
                f"expected revision {game.score_revision + 1}, got {change.revision}"
            )
        if (
            change.previous_left != game.trusted_left_score
            or change.previous_right != game.trusted_right_score
        ):
            raise ConflictingConfirmationError(
                "confirmation previous pair does not match current trusted score"
            )
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            trusted_left_score=change.new_left,
            trusted_right_score=change.new_right,
            score_revision=change.revision,
            last_confirmed_at=now,
            last_confirmed_timestamp_s=change.confirmed_timestamp_s,
            last_confirmed_frame_index=change.confirmed_frame_index,
            last_confirmed_epoch=change.confirmed_epoch,
            updated_at=now,
        )
        history.append(change)
        self._games[game_id] = updated
        self._history[game_id] = history
        return PersistResult(PersistOutcome.APPLIED, updated, change)

    def mark_acquisition_ended(self, game_id: UUID) -> Game:
        if self.fail_next_end:
            self.fail_next_end = False
            raise PersistError("injected end-acquisition write failure")
        game = self.get_game(game_id)
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            tracking_status=TrackingStatus.ACQUISITION_ENDED,
            updated_at=now,
        )
        self._games[game_id] = updated
        return updated

    def assert_current_matches_history(self, game_id: UUID) -> None:
        game = self.get_game(game_id)
        history = self.list_confirmed_changes(game_id)
        if game.score_revision == 0:
            if game.pair is not None or history:
                raise SchemaError("revision 0 invariant failed")
            return
        if not history or history[-1].revision != game.score_revision:
            raise SchemaError("current revision must equal newest event")
        if history[-1].new_pair != game.pair:
            raise SchemaError("current trusted score must equal newest event")

    def list_matchup_changes(self, game_id: UUID) -> tuple[ConfirmedMatchupChange, ...]:
        self.get_game(game_id)
        return tuple(self._matchup_history[game_id])

    def persist_matchup_confirmation(
        self, game_id: UUID, change: ConfirmedMatchupChange
    ) -> MatchupPersistResult:
        if self.fail_next_matchup_persist:
            self.fail_next_matchup_persist = False
            raise PersistError("injected matchup write failure")
        if change.left_team_id == change.right_team_id:
            raise ConflictingMatchupError("left and right team ids must differ")
        game = self.get_game(game_id)
        history = list(self._matchup_history[game_id])
        if change.revision == game.matchup_revision:
            stored = next((e for e in history if e.revision == change.revision), None)
            if stored is not None and matchup_payload_matches(stored, change):
                return MatchupPersistResult(PersistOutcome.IDEMPOTENT, game, stored)
            raise ConflictingMatchupError(
                f"matchup revision {change.revision} already persisted with different data"
            )
        if game.matchup_revision > 0:
            raise ConflictingMatchupError(
                "confirmed matchup cannot be rewritten; "
                f"persisted {game.confirmed_left_team_id}/{game.confirmed_right_team_id}"
            )
        if change.revision != 1:
            raise ConflictingMatchupError(
                f"expected matchup revision 1, got {change.revision}"
            )
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            confirmed_left_team_id=change.left_team_id,
            confirmed_right_team_id=change.right_team_id,
            matchup_revision=change.revision,
            matchup_confirmed_at=now,
            matchup_confirmed_timestamp_s=change.confirmed_timestamp_s,
            matchup_confirmed_frame_index=change.confirmed_frame_index,
            matchup_confirmed_epoch=change.confirmed_epoch,
            matchup_left_method=change.left_method.value,
            matchup_right_method=change.right_method.value,
            updated_at=now,
        )
        history.append(change)
        self._games[game_id] = updated
        self._matchup_history[game_id] = history
        return MatchupPersistResult(PersistOutcome.APPLIED, updated, change)

    def record_matchup_conflict(
        self, game_id: UUID, conflict_pair: tuple[str, str]
    ) -> Game:
        if self.fail_next_matchup_conflict:
            self.fail_next_matchup_conflict = False
            raise PersistError("injected matchup conflict write failure")
        game = self.get_game(game_id)
        now = datetime.now(tz=UTC)
        updated = self._replace(
            game,
            matchup_conflict_count=game.matchup_conflict_count + 1,
            last_matchup_conflict=f"{conflict_pair[0]}/{conflict_pair[1]}",
            updated_at=now,
        )
        self._games[game_id] = updated
        return updated

    def _replace(self, game: Game, **changes: object) -> Game:
        data = game.__dict__.copy()
        data.update(changes)
        return Game(**data)
