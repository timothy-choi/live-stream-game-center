"""Map persistence Game rows onto product schemas.

Does not expose OCR evidence, logo arrays, DSNs, or credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lsgc.api.schemas import (
    ConflictPair,
    GameSummary,
    MatchupStatus,
    MatchupSummary,
    ScoreStatus,
    ScoreSummary,
    ScoreTimelineEvent,
    TeamSummary,
)
from lsgc.identity.catalog import TeamCatalog
from lsgc.persist.models import Game, ScoreChangeRecord


def utc_iso(value: datetime | None) -> str | None:
    """Explicit ISO-8601 UTC with Z suffix. Naive datetimes are treated as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        aware = value.replace(tzinfo=UTC)
    else:
        aware = value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def require_utc_iso(value: datetime) -> str:
    stamp = utc_iso(value)
    if stamp is None:
        raise ValueError("timestamp is required")
    return stamp


def game_cursor(game: Game) -> str:
    return (
        f"{game.score_revision}:{game.matchup_revision}:{game.matchup_conflict_count}:"
        f"{game.platform_lifecycle.value}:{game.tracking_status.value}:"
        f"{int(game.tracking_enabled)}:{game.completion_status.value}:"
        f"{game.recovery_status.value}:{game.score_history_completeness.value}"
    )


def team_summary(team_id: str | None, catalog: TeamCatalog) -> TeamSummary | None:
    if not team_id:
        return None
    team = catalog.get(team_id)
    if team is None:
        return TeamSummary(id=team_id, display_name=team_id, abbreviation=team_id)
    return TeamSummary(
        id=team.team_id,
        display_name=team.display_name,
        abbreviation=team.abbreviation,
    )


def score_summary(game: Game) -> ScoreSummary:
    if game.score_revision <= 0 or game.pair is None:
        return ScoreSummary(status=ScoreStatus.UNCONFIRMED, left=None, right=None, revision=0)
    return ScoreSummary(
        status=ScoreStatus.TRUSTED,
        left=game.trusted_left_score,
        right=game.trusted_right_score,
        revision=game.score_revision,
    )


def matchup_summary(game: Game, catalog: TeamCatalog) -> MatchupSummary:
    conflict_pair = None
    raw = game.last_matchup_conflict
    if raw and "/" in raw:
        left, _, right = raw.partition("/")
        if left and right and "/" not in right:
            conflict_pair = ConflictPair(left_team_id=left, right_team_id=right)
    if game.matchup_revision <= 0:
        return MatchupSummary(
            status=MatchupStatus.UNCONFIRMED,
            left_team=None,
            right_team=None,
            revision=0,
            conflict_pair=conflict_pair,
            conflict_count=game.matchup_conflict_count,
        )
    status = (
        MatchupStatus.CONFLICT if game.matchup_conflict_count > 0 else MatchupStatus.CONFIRMED
    )
    return MatchupSummary(
        status=status,
        left_team=team_summary(game.confirmed_left_team_id, catalog),
        right_team=team_summary(game.confirmed_right_team_id, catalog),
        revision=game.matchup_revision,
        conflict_pair=conflict_pair,
        conflict_count=game.matchup_conflict_count,
    )


def game_summary(game: Game, catalog: TeamCatalog) -> GameSummary:
    return GameSummary(
        id=game.id,
        platform=game.platform,
        external_broadcast_id=game.external_broadcast_id,
        channel_id=game.channel_id,
        title=game.title,
        platform_lifecycle=game.platform_lifecycle.value,
        tracking_status=game.tracking_status.value,
        tracking_enabled=game.tracking_enabled,
        scheduled_start_at=utc_iso(game.scheduled_start_at),
        actual_start_at=utc_iso(game.actual_start_at),
        actual_end_at=utc_iso(game.actual_end_at),
        matchup=matchup_summary(game, catalog),
        score=score_summary(game),
        completion_status=game.completion_status.value,
        final_score_verified=game.final_score_verified,
        postgame_left=game.postgame_left_score,
        postgame_right=game.postgame_right_score,
        final_score_conflict=game.final_score_conflict,
        recovery_status=game.recovery_status.value,
        score_history_completeness=game.score_history_completeness.value,
        last_updated_at=require_utc_iso(game.updated_at),
        cursor=game_cursor(game),
    )


def timeline_event(record: ScoreChangeRecord) -> ScoreTimelineEvent:
    change = record.change
    return ScoreTimelineEvent(
        revision=change.revision,
        previous_left=change.previous_left,
        previous_right=change.previous_right,
        left=change.new_left,
        right=change.new_right,
        initialization=change.initialization,
        confirmed_at=require_utc_iso(record.persisted_at),
        observed_timestamp_s=change.confirmed_timestamp_s,
        source=change.source.value,
    )
