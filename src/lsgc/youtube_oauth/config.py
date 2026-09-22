"""Application Google OAuth configuration.

These are APPLICATION credentials. Ordinary users never supply them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from lsgc.youtube_oauth.errors import OAuthNotConfiguredError

GOOGLE_CLIENT_ID_ENV = "LSGC_GOOGLE_CLIENT_ID"
GOOGLE_CLIENT_SECRET_ENV = "LSGC_GOOGLE_CLIENT_SECRET"
GOOGLE_REDIRECT_URI_ENV = "LSGC_GOOGLE_REDIRECT_URI"
TOKEN_ENCRYPTION_KEY_ENV = "LSGC_TOKEN_ENCRYPTION_KEY"
ALLOW_PLAINTEXT_ENV = "LSGC_ALLOW_PLAINTEXT_OAUTH_TOKENS"
WEB_ORIGIN_ENV = "LSGC_WEB_ORIGIN"
STATE_TTL_ENV = "LSGC_OAUTH_STATE_TTL_S"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/api/youtube/oauth/callback"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
DEFAULT_STATE_TTL_S = 600.0


@dataclass(frozen=True)
class GoogleOAuthSettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str
    encryption_key: str | None
    allow_plaintext_tokens: bool
    web_origin: str
    state_ttl_s: float

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


def _env_bool(name: str, default: bool, environ: dict[str, str]) -> bool:
    raw = (environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load_google_oauth_settings(*, environ: dict[str, str] | None = None) -> GoogleOAuthSettings:
    env = dict(os.environ if environ is None else environ)
    ttl_raw = (env.get(STATE_TTL_ENV) or "").strip()
    ttl = float(ttl_raw) if ttl_raw else DEFAULT_STATE_TTL_S
    if ttl <= 0:
        raise OAuthNotConfiguredError(f"{STATE_TTL_ENV} must be > 0")
    origins = (env.get("LSGC_CORS_ORIGINS") or "").strip()
    first_origin = next((part.strip() for part in origins.split(",") if part.strip()), "")
    web_origin = (env.get(WEB_ORIGIN_ENV) or first_origin or "http://127.0.0.1:5173").rstrip("/")
    return GoogleOAuthSettings(
        client_id=(env.get(GOOGLE_CLIENT_ID_ENV) or "").strip(),
        client_secret=(env.get(GOOGLE_CLIENT_SECRET_ENV) or "").strip(),
        redirect_uri=(env.get(GOOGLE_REDIRECT_URI_ENV) or DEFAULT_REDIRECT_URI).strip(),
        scope=YOUTUBE_READONLY_SCOPE,
        encryption_key=(env.get(TOKEN_ENCRYPTION_KEY_ENV) or "").strip() or None,
        allow_plaintext_tokens=_env_bool(ALLOW_PLAINTEXT_ENV, False, env),
        web_origin=web_origin,
        state_ttl_s=ttl,
    )
