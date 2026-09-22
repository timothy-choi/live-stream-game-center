"""Helpers for YouTube OAuth tests. Never assert raw token values in logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from lsgc.broadcast.calibration import MappingCalibrationLookup
from lsgc.youtube_oauth.config import GoogleOAuthSettings, YOUTUBE_READONLY_SCOPE
from lsgc.youtube_oauth.crypto import TokenCipher
from lsgc.youtube_oauth.service import YoutubeConnectionService
from lsgc.youtube_oauth.tokens import GoogleTokenClient


FERNET_KEY = Fernet.generate_key().decode("ascii")


class ScriptedFormTransport:
    def __init__(self, responses: list[dict] | None = None, errors: list[Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.calls: list[tuple[str, dict]] = []

    def post_form(self, url: str, data):
        self.calls.append((url, dict(data)))
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            raise AssertionError("no scripted token response")
        return self.responses.pop(0)


class ScriptedYoutubeClient:
    def __init__(self, channel_id: str = "UCz3q0f-dMPNIyQhntPTE7gQ", title: str = "Arcade") -> None:
        self.channel_id = channel_id
        self.title = title
        self.access_tokens: list[str] = []

    def list_authorized_channel(self):
        return self.channel_id, self.title

    def list_channel_broadcasts(self, channel_id: str):
        return ()


def settings(**changes) -> GoogleOAuthSettings:
    data = dict(
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-client-secret",
        redirect_uri="http://127.0.0.1:8000/api/youtube/oauth/callback",
        scope=YOUTUBE_READONLY_SCOPE,
        encryption_key=FERNET_KEY,
        allow_plaintext_tokens=False,
        web_origin="http://127.0.0.1:5173",
        state_ttl_s=600.0,
    )
    data.update(changes)
    return GoogleOAuthSettings(**data)


def token_payload(*, refresh: str | None = "refresh-token-value", access: str = "access-token-value") -> dict:
    payload = {
        "access_token": access,
        "expires_in": 3600,
        "scope": YOUTUBE_READONLY_SCOPE,
        "token_type": "Bearer",
    }
    if refresh is not None:
        payload["refresh_token"] = refresh
    return payload


def make_service(store, *, transport=None, youtube=None, lookup=None, clock=None):
    cfg = settings()
    client = youtube or ScriptedYoutubeClient()

    def factory(access_token: str):
        client.access_tokens.append(access_token)
        return client

    now = clock or (lambda: datetime(2026, 9, 21, 20, 0, tzinfo=UTC))
    return YoutubeConnectionService(
        store,
        cfg,
        cipher=TokenCipher(key=cfg.encryption_key, allow_plaintext=False),
        tokens=GoogleTokenClient(
            cfg,
            transport=transport or ScriptedFormTransport([token_payload()]),
            clock=now,
        ),
        calibration=lookup or MappingCalibrationLookup(),
        youtube_client_factory=factory,
        clock=now,
    )
