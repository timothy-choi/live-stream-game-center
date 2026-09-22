"""Durable persistence of M7 trusted visual score state.

PostgreSQL is the recovery source for confirmed scores. Unconfirmed candidate
evidence is not stored. Acquisition-ended is not a game-final.

Knows nothing of yt-dlp, FFmpeg, or REST.
"""

from lsgc.persist.config import DATABASE_URL_ENV, TEST_DATABASE_URL_ENV, database_url
from lsgc.persist.errors import (
    ConfigError,
    ConflictingConfirmationError,
    ConflictingMatchupError,
    PersistError,
    SchemaError,
    StaleConfirmationError,
    UnknownGameError,
)
from lsgc.persist.migrate import apply_migrations, connect
from lsgc.persist.models import (
    PLATFORM_YOUTUBE,
    CompletionStatus,
    Game,
    MatchupPersistResult,
    PersistOutcome,
    PersistResult,
    PlatformLifecycle,
    ScoreChangeRecord,
    TrackingStatus,
)
from lsgc.persist.pool import PooledGameRepository, open_pool
from lsgc.persist.repository import (
    PostgresGameRepository,
    event_payload_matches,
    matchup_payload_matches,
)
from lsgc.persist.finalizer import DurableFinalizationTracker
from lsgc.persist.tracker import DurableMatchupTracker, DurableScoreTracker

__all__ = [
    "DATABASE_URL_ENV",
    "PLATFORM_YOUTUBE",
    "TEST_DATABASE_URL_ENV",
    "CompletionStatus",
    "ConfigError",
    "ConflictingConfirmationError",
    "ConflictingMatchupError",
    "DurableFinalizationTracker",
    "DurableMatchupTracker",
    "DurableScoreTracker",
    "Game",
    "MatchupPersistResult",
    "PersistError",
    "PersistOutcome",
    "PersistResult",
    "PlatformLifecycle",
    "PooledGameRepository",
    "PostgresGameRepository",
    "SchemaError",
    "ScoreChangeRecord",
    "StaleConfirmationError",
    "TrackingStatus",
    "UnknownGameError",
    "apply_migrations",
    "connect",
    "database_url",
    "event_payload_matches",
    "matchup_payload_matches",
    "open_pool",
]
