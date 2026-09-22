"""YouTube connection use cases. Never returns token values."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from lsgc.broadcast.calibration import CalibrationLookup
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.youtube import YouTubeDataApiClient
from lsgc.persist.errors import PersistError, UnknownGameError
from lsgc.youtube_oauth.authorize import build_authorization_url, new_state, state_record
from lsgc.youtube_oauth.channel import authorized_channel
from lsgc.youtube_oauth.config import GoogleOAuthSettings
from lsgc.youtube_oauth.crypto import TokenCipher
from lsgc.youtube_oauth.errors import (
    AuthorizationDeniedError,
    InvalidOAuthStateError,
    OAuthNotConfiguredError,
    TokenExchangeError,
    YoutubeChannelLookupError,
    YoutubeOAuthError,
)
from lsgc.youtube_oauth.models import (
    CalibrationReadiness,
    ConnectionStatus,
    TokenBundle,
    YoutubeConnection,
)
from lsgc.youtube_oauth.tokens import GoogleTokenClient


class YoutubeConnectionService:
    def __init__(
        self,
        store,
        settings: GoogleOAuthSettings,
        *,
        cipher: TokenCipher,
        tokens: GoogleTokenClient,
        calibration: CalibrationLookup,
        youtube_client_factory=None,
        clock=None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._cipher = cipher
        self._tokens = tokens
        self._calibration = calibration
        self._youtube_client_factory = youtube_client_factory or (
            lambda access_token: YouTubeDataApiClient(access_token=access_token)
        )
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._refresh_locks: dict[UUID, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def start_authorization(self) -> str:
        if not self._settings.configured:
            raise OAuthNotConfiguredError("Google OAuth client is not configured")
        state = new_state()
        record = state_record(self._settings, state, now=self._clock())
        self._store.create_oauth_state(record.state, expires_at=record.expires_at)
        return build_authorization_url(self._settings, state)

    def complete_authorization(self, *, code: str | None, state: str | None, error: str | None) -> YoutubeConnection:
        if error:
            raise AuthorizationDeniedError("YouTube authorization was denied")
        if not state:
            raise InvalidOAuthStateError("missing oauth state")
        try:
            self._store.consume_oauth_state(state, now=self._clock())
        except PersistError as exc:
            message = str(exc)
            if "expired" in message:
                raise InvalidOAuthStateError("oauth state expired") from exc
            if "already used" in message:
                raise InvalidOAuthStateError("oauth state already used") from exc
            raise InvalidOAuthStateError("invalid oauth state") from exc
        if not code:
            raise TokenExchangeError("authorization code missing")
        bundle = self._tokens.exchange_code(code)
        client = self._youtube_client_factory(bundle.access_token)
        channel_id, title = authorized_channel(client)
        return self._store.upsert_connected_channel(
            youtube_channel_id=channel_id,
            youtube_channel_title=title,
            granted_scopes=bundle.scope or self._settings.scope,
            refresh_token_ciphertext=self._cipher.encrypt(bundle.refresh_token),
            access_token_ciphertext=self._cipher.encrypt(bundle.access_token),
            access_token_expires_at=bundle.expires_at,
            token_type=bundle.token_type,
        )

    def list_connections(self) -> tuple[YoutubeConnection, ...]:
        return self._store.list_connections()

    def calibration_for(self, connection: YoutubeConnection) -> CalibrationReadiness:
        bundle = self._calibration.get(connection.youtube_channel_id)
        return (
            CalibrationReadiness.READY
            if bundle is not None
            else CalibrationReadiness.NEEDS_CALIBRATION
        )

    def disconnect(self, connection_id: UUID) -> YoutubeConnection:
        connection = self._store.get_connection(connection_id)
        refresh = self._cipher.decrypt(connection.refresh_token_ciphertext)
        access = self._cipher.decrypt(connection.access_token_ciphertext)
        token = refresh or access
        if token:
            self._tokens.revoke(token)
        return self._store.mark_disconnected(connection_id)

    def access_token_for(self, connection: YoutubeConnection) -> str:
        """Return a usable access token, refreshing if needed. Never log it."""
        if connection.status is not ConnectionStatus.CONNECTED:
            raise TokenExchangeError("connection is not authorized")
        lock = self._lock_for(connection.id)
        with lock:
            current = self._store.get_connection(connection.id)
            access = self._cipher.decrypt(current.access_token_ciphertext)
            expires = current.access_token_expires_at
            now = self._clock()
            if access and expires is not None:
                aware = expires if expires.tzinfo else expires.replace(tzinfo=UTC)
                if aware - timedelta(seconds=60) > now:
                    return access
            refresh = self._cipher.decrypt(current.refresh_token_ciphertext)
            if not refresh:
                self._store.mark_needs_reauthorization(current.id)
                raise TokenExchangeError("refresh authorization is missing")
            try:
                bundle = self._tokens.refresh(refresh)
            except TokenExchangeError:
                self._store.mark_needs_reauthorization(current.id)
                raise
            self._store.update_tokens(
                current.id,
                refresh_token_ciphertext=self._cipher.encrypt(bundle.refresh_token),
                access_token_ciphertext=self._cipher.encrypt(bundle.access_token),
                access_token_expires_at=bundle.expires_at,
                token_type=bundle.token_type,
                granted_scopes=bundle.scope,
            )
            return bundle.access_token

    def frontend_redirect(self, *, error: str | None = None) -> str:
        origin = self._settings.web_origin.rstrip("/")
        if error:
            return f"{origin}/youtube/connected?{urlencode({'error': error})}"
        return f"{origin}/youtube/connected?ok=1"

    def public_error_code(self, exc: Exception) -> str:
        if isinstance(exc, AuthorizationDeniedError):
            return "denied"
        if isinstance(exc, InvalidOAuthStateError):
            text = str(exc)
            if "expired" in text:
                return "expired_state"
            if "already used" in text:
                return "replayed_state"
            return "invalid_state"
        if isinstance(exc, OAuthNotConfiguredError):
            return "not_configured"
        if isinstance(exc, YoutubeChannelLookupError):
            return "no_channel"
        if isinstance(exc, TokenExchangeError):
            return "exchange_failed"
        if isinstance(exc, UnknownGameError):
            return "not_found"
        if isinstance(exc, YoutubeOAuthError):
            return "oauth_failed"
        return "oauth_failed"

    def _lock_for(self, connection_id: UUID) -> threading.Lock:
        with self._locks_guard:
            lock = self._refresh_locks.get(connection_id)
            if lock is None:
                lock = threading.Lock()
                self._refresh_locks[connection_id] = lock
            return lock


def maybe_connection_service(store, calibration) -> YoutubeConnectionService | None:
    """Build a service when application OAuth credentials are present."""
    from lsgc.youtube_oauth.config import load_google_oauth_settings
    from lsgc.youtube_oauth.crypto import TokenCipher
    from lsgc.youtube_oauth.tokens import GoogleTokenClient

    settings = load_google_oauth_settings()
    if not settings.configured:
        return None
    try:
        cipher = TokenCipher(
            key=settings.encryption_key,
            allow_plaintext=settings.allow_plaintext_tokens,
        )
    except OAuthNotConfiguredError:
        return None
    return YoutubeConnectionService(
        store,
        settings,
        cipher=cipher,
        tokens=GoogleTokenClient(settings),
        calibration=calibration,
    )


def safe_error_detail(exc: Exception) -> str:
    return sanitize_control_plane_text(str(exc)) or "youtube connection error"
