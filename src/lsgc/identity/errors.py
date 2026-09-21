"""Identity-recognition errors. Not ingestion, score OCR, or persistence."""


class IdentityError(Exception):
    """Base class for team-identity failures."""


class CatalogError(IdentityError):
    """Canonical team catalog is malformed."""


class LogoSchemaError(IdentityError):
    """Serialized logo templates are malformed or an unsupported version."""


class LogoMismatchError(IdentityError):
    """Logo templates do not belong with the profile they were paired with."""


class IdentityPolicyError(IdentityError):
    """Matchup confirmation policy is malformed."""


class IdentityRestoreError(IdentityError):
    """Durable confirmed matchup cannot be restored."""
