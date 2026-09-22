"""Google OAuth token exchange, refresh, and revocation.

Official endpoints:
  POST https://oauth2.googleapis.com/token
  POST https://oauth2.googleapis.com/revoke

Never log request bodies or token values.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

import certifi

from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.youtube_oauth.config import (
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    GoogleOAuthSettings,
)
from lsgc.youtube_oauth.errors import TokenExchangeError
from lsgc.youtube_oauth.models import TokenBundle


class FormTransport(Protocol):
    def post_form(self, url: str, data: Mapping[str, str]) -> dict: ...


class UrllibFormTransport:
    def __init__(self, *, timeout_s: float = 20.0) -> None:
        self._timeout_s = timeout_s
        self._context = ssl.create_default_context(cafile=certifi.where())

    def post_form(self, url: str, data: Mapping[str, str]) -> dict:
        body = urllib.parse.urlencode(dict(data)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_s, context=self._context
            ) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise TokenExchangeError(
                sanitize_control_plane_text(_google_error_message(raw, exc.code))
                or "token request failed"
            ) from None
        except urllib.error.URLError as exc:
            raise TokenExchangeError("token endpoint unreachable") from exc
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise TokenExchangeError("malformed token response") from exc
        if not isinstance(parsed, dict):
            raise TokenExchangeError("malformed token response")
        return parsed


class GoogleTokenClient:
    def __init__(
        self,
        settings: GoogleOAuthSettings,
        *,
        transport: FormTransport | None = None,
        clock=None,
    ) -> None:
        self._settings = settings
        self._transport = transport or UrllibFormTransport()
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def exchange_code(self, code: str) -> TokenBundle:
        payload = self._transport.post_form(
            GOOGLE_TOKEN_ENDPOINT,
            {
                "code": code,
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "redirect_uri": self._settings.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        return self._bundle(payload, require_refresh=True)

    def refresh(self, refresh_token: str) -> TokenBundle:
        payload = self._transport.post_form(
            GOOGLE_TOKEN_ENDPOINT,
            {
                "refresh_token": refresh_token,
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "grant_type": "refresh_token",
            },
        )
        bundle = self._bundle(payload, require_refresh=False)
        return TokenBundle(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token or refresh_token,
            expires_at=bundle.expires_at,
            token_type=bundle.token_type,
            scope=bundle.scope,
        )

    def revoke(self, token: str) -> None:
        try:
            self._transport.post_form(GOOGLE_REVOKE_ENDPOINT, {"token": token})
        except TokenExchangeError:
            # Local disconnect still proceeds; Google-side revoke is best-effort.
            return

    def _bundle(self, payload: Mapping[str, object], *, require_refresh: bool) -> TokenBundle:
        access = str(payload.get("access_token") or "").strip()
        refresh = str(payload.get("refresh_token") or "").strip() or None
        if not access:
            raise TokenExchangeError("token response missing access_token")
        if require_refresh and not refresh:
            raise TokenExchangeError("token response missing refresh_token")
        expires_in = payload.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_at = self._clock() + timedelta(seconds=float(expires_in))
        scope = str(payload.get("scope") or "").strip() or None
        token_type = str(payload.get("token_type") or "").strip() or None
        return TokenBundle(
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            token_type=token_type,
            scope=scope,
        )


def _google_error_message(raw: str, status: int) -> str:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return f"token request failed ({status})"
    if not isinstance(body, dict):
        return f"token request failed ({status})"
    error = str(body.get("error") or "").strip() or "token_error"
    return error
