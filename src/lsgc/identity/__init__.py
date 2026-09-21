"""Team identity observation, temporal matchup confirmation, and logo templates.

A raw identity observation is never a confirmed matchup. Confirmed matchup is
temporally validated visual scoreboard identity — not schedule truth, not
title-derived truth, and not owner-verified truth.

Knows nothing of yt-dlp, FFmpeg, YouTube titles, or PostgreSQL.
"""

from lsgc.identity.catalog import (
    DEFAULT_CATALOG,
    NFL_TEAMS,
    TeamCatalog,
    TeamIdentity,
    mentioned_team_ids,
    normalize_identity_text,
    title_visual_disagreement,
)
from lsgc.identity.errors import (
    CatalogError,
    IdentityError,
    IdentityPolicyError,
    IdentityRestoreError,
    LogoMismatchError,
    LogoSchemaError,
)
from lsgc.identity.logos import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    LOGO_HEIGHT,
    LOGO_SCHEMA_VERSION,
    LOGO_WIDTH,
    LogoRecognizer,
    LogoTemplate,
    LogoTemplateSet,
    build_logo_templates,
    extract_identity_crops,
    load_logo_templates,
    load_logo_templates_unchecked,
    save_logo_templates,
)
from lsgc.identity.models import (
    CandidateMatchup,
    ConfirmedMatchupChange,
    ConfirmedMatchupState,
    IdentityMethod,
    MatchupApplyOutcome,
    MatchupApplyResult,
    MatchupObservation,
    TeamIdentityObservation,
    UnreadableIdentityReason,
)
from lsgc.identity.pipeline import MatchupObservationPipeline
from lsgc.identity.policy import MatchupConfirmationPolicy
from lsgc.identity.recognizer import IdentityRecognizer
from lsgc.identity.text import UnvalidatedTextReader, resolve_normalized_text
from lsgc.identity.validator import TemporalMatchupValidator

__all__ = [
    "DEFAULT_CATALOG",
    "DEFAULT_MIN_MARGIN",
    "DEFAULT_MIN_SCORE",
    "LOGO_HEIGHT",
    "LOGO_SCHEMA_VERSION",
    "LOGO_WIDTH",
    "NFL_TEAMS",
    "CandidateMatchup",
    "CatalogError",
    "ConfirmedMatchupChange",
    "ConfirmedMatchupState",
    "IdentityError",
    "IdentityMethod",
    "IdentityPolicyError",
    "IdentityRecognizer",
    "IdentityRestoreError",
    "LogoMismatchError",
    "LogoRecognizer",
    "LogoSchemaError",
    "LogoTemplate",
    "LogoTemplateSet",
    "MatchupApplyOutcome",
    "MatchupApplyResult",
    "MatchupConfirmationPolicy",
    "MatchupObservation",
    "MatchupObservationPipeline",
    "TeamCatalog",
    "TeamIdentity",
    "TeamIdentityObservation",
    "TemporalMatchupValidator",
    "UnreadableIdentityReason",
    "UnvalidatedTextReader",
    "build_logo_templates",
    "extract_identity_crops",
    "load_logo_templates",
    "load_logo_templates_unchecked",
    "mentioned_team_ids",
    "normalize_identity_text",
    "resolve_normalized_text",
    "save_logo_templates",
    "title_visual_disagreement",
]
