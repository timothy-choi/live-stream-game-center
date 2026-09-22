"""Durable YouTube connection types. Token values are not product fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    NEEDS_REAUTHORIZATION = "NEEDS_REAUTHORIZATION"
    DISCONNECTED = "DISCONNECTED"


class CalibrationReadiness(str, Enum):
    READY = "READY"
    NEEDS_CALIBRATION = "NEEDS_CALIBRATION"


@dataclass(frozen=True)
class YoutubeConnection:
    """One authorized YouTube channel. Secrets stay on the ciphertext fields."""

    id: UUID
    provider: str
    youtube_channel_id: str
    youtube_channel_title: str | None
    status: ConnectionStatus
    granted_scopes: str | None
    connected_at: datetime
    created_at: datetime
    updated_at: datetime
    last_refreshed_at: datetime | None = None
    last_used_at: datetime | None = None
    disconnected_at: datetime | None = None
    owner_user_id: UUID | None = None
    refresh_token_ciphertext: str | None = None
    access_token_ciphertext: str | None = None
    access_token_expires_at: datetime | None = None
    token_type: str | None = None


@dataclass(frozen=True)
class OAuthStateRecord:
    state: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class TokenBundle:
    """In-memory tokens after decrypt. Never serialize to API or logs."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    token_type: str | None
    scope: str | None
