"""Build the Google authorization URL. Client secret is never included."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from lsgc.youtube_oauth.config import GOOGLE_AUTH_ENDPOINT, GoogleOAuthSettings
from lsgc.youtube_oauth.errors import OAuthNotConfiguredError
from lsgc.youtube_oauth.models import OAuthStateRecord


def new_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorization_url(settings: GoogleOAuthSettings, state: str) -> str:
    if not settings.configured:
        raise OAuthNotConfiguredError("Google OAuth client is not configured")
    query = urlencode(
        {
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "response_type": "code",
            "scope": settings.scope,
            "access_type": "offline",
            "include_granted_scopes": "false",
            "prompt": "consent select_account",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}"


def state_record(settings: GoogleOAuthSettings, state: str, *, now: datetime | None = None) -> OAuthStateRecord:
    created = now or datetime.now(tz=UTC)
    return OAuthStateRecord(
        state=state,
        created_at=created,
        expires_at=created + timedelta(seconds=settings.state_ttl_s),
    )
