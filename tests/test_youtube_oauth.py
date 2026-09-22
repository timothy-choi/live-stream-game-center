"""YouTube OAuth connection tests. Offline. Tokens must not appear in API bodies."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from lsgc.api.app import create_app
from lsgc.api.config import ApiSettings
from lsgc.api.hub import GameEventHub
from lsgc.broadcast.calibration import MappingCalibrationLookup
from lsgc.identity.catalog import DEFAULT_CATALOG
from lsgc.persist.errors import PersistError
from lsgc.youtube_oauth.authorize import build_authorization_url
from lsgc.youtube_oauth.config import (
    GOOGLE_AUTH_ENDPOINT,
    GOOGLE_REVOKE_ENDPOINT,
    YOUTUBE_READONLY_SCOPE,
)
from lsgc.youtube_oauth.crypto import TokenCipher
from lsgc.youtube_oauth.errors import (
    InvalidOAuthStateError,
    OAuthNotConfiguredError,
    TokenExchangeError,
    YoutubeChannelLookupError,
)
from lsgc.youtube_oauth.models import ConnectionStatus
from lsgc.youtube_oauth.tokens import GoogleTokenClient
from oauth_support import (
    FERNET_KEY,
    ScriptedFormTransport,
    ScriptedYoutubeClient,
    make_service,
    settings,
    token_payload,
)
from persist_memory import InMemoryGameRepository
from tests.broadcast_fakes import DummyCalibration


def _client(store, service=None):
    hub = GameEventHub(DEFAULT_CATALOG)
    app = create_app(
        store=store,
        hub=hub,
        settings=ApiSettings(
            cors_origins=("http://127.0.0.1:5173",),
            run_orchestrator=False,
            heartbeat_s=15,
            max_queue=8,
        ),
        catalog=DEFAULT_CATALOG,
        connection_service=service,
        calibration=MappingCalibrationLookup(),
    )
    return TestClient(app)


def test_authorization_url_has_required_parameters() -> None:
    url = build_authorization_url(settings(), "state-value")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert url.startswith(GOOGLE_AUTH_ENDPOINT)
    assert query["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/api/youtube/oauth/callback"]
    assert query["scope"] == [YOUTUBE_READONLY_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-value"]
    assert "consent" in query["prompt"][0]
    assert "client_secret" not in url
    assert "test-client-secret" not in url


def test_start_persists_state_and_redirects() -> None:
    store = InMemoryGameRepository()
    service = make_service(store)
    client = _client(store, service)
    response = client.get("/api/youtube/oauth/start", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(GOOGLE_AUTH_ENDPOINT)
    assert "test-client-secret" not in location
    state = parse_qs(urlparse(location).query)["state"][0]
    with pytest.raises(PersistError):
        store.consume_oauth_state("wrong")
    store.consume_oauth_state(state, now=datetime(2026, 9, 21, 20, 0, tzinfo=UTC))


def test_callback_rejects_missing_wrong_expired_and_replayed_state() -> None:
    store = InMemoryGameRepository()
    service = make_service(store)
    client = _client(store, service)
    start = client.get("/api/youtube/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    missing = client.get("/api/youtube/oauth/callback", follow_redirects=False)
    assert "invalid_state" in missing.headers["location"]

    wrong = client.get(
        "/api/youtube/oauth/callback",
        params={"code": "x", "state": "nope"},
        follow_redirects=False,
    )
    assert "invalid_state" in wrong.headers["location"]

    expired_store = InMemoryGameRepository()
    expired_service = make_service(
        expired_store,
        clock=lambda: datetime(2026, 9, 21, 20, 0, tzinfo=UTC),
    )
    expired_store.create_oauth_state(
        "stale",
        expires_at=datetime(2026, 9, 21, 19, 0, tzinfo=UTC),
    )
    with pytest.raises(InvalidOAuthStateError):
        expired_service.complete_authorization(code="abc", state="stale", error=None)

    store.consume_oauth_state(state, now=datetime(2026, 9, 21, 20, 0, tzinfo=UTC))
    replay = client.get(
        "/api/youtube/oauth/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert "replayed_state" in replay.headers["location"]


def test_callback_denied_does_not_exchange() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport([token_payload()])
    service = make_service(store, transport=transport)
    client = _client(store, service)
    start = client.get("/api/youtube/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = client.get(
        "/api/youtube/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert "denied" in response.headers["location"]
    assert transport.calls == []


def test_successful_callback_persists_exact_channel() -> None:
    store = InMemoryGameRepository()
    service = make_service(
        store,
        transport=ScriptedFormTransport([token_payload()]),
        youtube=ScriptedYoutubeClient("UCz3q0f-dMPNIyQhntPTE7gQ", "Arcade"),
    )
    client = _client(store, service)
    start = client.get("/api/youtube/oauth/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = client.get(
        "/api/youtube/oauth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/youtube/connected?ok=1")
    listed = client.get("/api/youtube/connections").json()
    assert listed["connections"][0]["youtube_channel_id"] == "UCz3q0f-dMPNIyQhntPTE7gQ"
    assert listed["connections"][0]["youtube_channel_title"] == "Arcade"
    assert listed["connections"][0]["status"] == "CONNECTED"
    assert listed["connections"][0]["calibration_status"] == "NEEDS_CALIBRATION"
    raw = str(listed)
    assert "access-token-value" not in raw
    assert "refresh-token-value" not in raw
    assert "test-client-secret" not in raw
    assert "access_token" not in raw
    assert "refresh_token" not in raw
    assert "client_secret" not in raw


def test_matching_calibration_is_ready() -> None:
    store = InMemoryGameRepository()
    lookup = MappingCalibrationLookup()
    lookup.bind("UCz3q0f-dMPNIyQhntPTE7gQ", DummyCalibration("UCz3q0f-dMPNIyQhntPTE7gQ"))
    service = make_service(store, lookup=lookup)
    start = service.start_authorization()
    state = parse_qs(urlparse(start).query)["state"][0]
    connection = service.complete_authorization(code="code", state=state, error=None)
    assert service.calibration_for(connection).value == "READY"


def test_reconnect_same_channel_updates() -> None:
    store = InMemoryGameRepository()
    service = make_service(
        store,
        transport=ScriptedFormTransport(
            [token_payload(), token_payload(access="access-2", refresh="refresh-2")]
        ),
    )
    first_state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    first = service.complete_authorization(code="a", state=first_state, error=None)
    second_state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    second = service.complete_authorization(code="b", state=second_state, error=None)
    assert first.id == second.id
    assert len(store.list_connections()) == 1


def test_refresh_uses_new_access_token_and_keeps_refresh() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport(
        [token_payload(), {"access_token": "access-2", "expires_in": 3600, "token_type": "Bearer"}]
    )
    clock_now = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)

    def clock():
        return clock_now

    service = make_service(store, transport=transport, clock=clock)
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    connection = service.complete_authorization(code="a", state=state, error=None)
    clock_now = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
    token = service.access_token_for(connection)
    assert token == "access-2"
    stored = store.get_connection(connection.id)
    assert stored.status is ConnectionStatus.CONNECTED
    cipher = TokenCipher(key=FERNET_KEY, allow_plaintext=False)
    assert cipher.decrypt(stored.refresh_token_ciphertext) == "refresh-token-value"


def test_refresh_failure_marks_needs_reauthorization() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport([token_payload()])
    service = make_service(store, transport=transport)
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    connection = service.complete_authorization(code="a", state=state, error=None)
    transport.errors.append(TokenExchangeError("invalid_grant"))
    store.update_tokens(
        connection.id,
        refresh_token_ciphertext=connection.refresh_token_ciphertext,
        access_token_ciphertext=connection.access_token_ciphertext,
        access_token_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        token_type="Bearer",
        granted_scopes=None,
    )
    with pytest.raises(TokenExchangeError):
        service.access_token_for(store.get_connection(connection.id))
    assert store.get_connection(connection.id).status is ConnectionStatus.NEEDS_REAUTHORIZATION


def test_concurrent_refresh_does_not_corrupt_state() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport(
        [
            token_payload(),
            {"access_token": "access-2", "expires_in": 3600, "token_type": "Bearer"},
        ]
    )
    service = make_service(store, transport=transport)
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    connection = service.complete_authorization(code="a", state=state, error=None)
    store.update_tokens(
        connection.id,
        refresh_token_ciphertext=store.get_connection(connection.id).refresh_token_ciphertext,
        access_token_ciphertext=store.get_connection(connection.id).access_token_ciphertext,
        access_token_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        token_type="Bearer",
        granted_scopes=None,
    )
    results: list[str] = []

    def worker() -> None:
        results.append(service.access_token_for(store.get_connection(connection.id)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ["access-2", "access-2"]
    assert store.get_connection(connection.id).status is ConnectionStatus.CONNECTED


def test_disconnect_clears_tokens() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport([token_payload(), {}])
    service = make_service(store, transport=transport)
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    connection = service.complete_authorization(code="a", state=state, error=None)
    disconnected = service.disconnect(connection.id)
    assert disconnected.status is ConnectionStatus.DISCONNECTED
    assert disconnected.refresh_token_ciphertext is None
    assert any(url == GOOGLE_REVOKE_ENDPOINT for url, _ in transport.calls)


def test_exchange_failure_and_missing_channel() -> None:
    store = InMemoryGameRepository()
    transport = ScriptedFormTransport(errors=[TokenExchangeError("invalid_grant")])
    service = make_service(store, transport=transport)
    state = parse_qs(urlparse(service.start_authorization()).query)["state"][0]
    with pytest.raises(TokenExchangeError):
        service.complete_authorization(code="bad", state=state, error=None)

    store2 = InMemoryGameRepository()
    youtube = ScriptedYoutubeClient("", "")
    youtube.channel_id = None
    youtube.title = None
    service2 = make_service(
        store2,
        transport=ScriptedFormTransport([token_payload()]),
        youtube=youtube,
    )
    state2 = parse_qs(urlparse(service2.start_authorization()).query)["state"][0]
    with pytest.raises(YoutubeChannelLookupError):
        service2.complete_authorization(code="ok", state=state2, error=None)


def test_malformed_token_response() -> None:
    client = GoogleTokenClient(settings(), transport=ScriptedFormTransport([{"token_type": "Bearer"}]))
    with pytest.raises(TokenExchangeError, match="access_token"):
        client.exchange_code("code")


def test_fernet_required_without_explicit_plaintext() -> None:
    with pytest.raises(OAuthNotConfiguredError):
        TokenCipher(key=None, allow_plaintext=False)


def test_start_without_service_redirects_not_configured() -> None:
    store = InMemoryGameRepository()
    app = create_app(
        store=store,
        settings=ApiSettings(cors_origins=(), run_orchestrator=False, heartbeat_s=15, max_queue=8),
        connection_service=None,
        calibration=MappingCalibrationLookup(),
    )
    client = TestClient(app)
    listed = client.get("/api/youtube/connections")
    assert listed.status_code == 200
    assert listed.json() == {"connections": []}
    start = client.get("/api/youtube/oauth/start", follow_redirects=False)
    assert "not_configured" in start.headers["location"]


def test_developer_channels_are_optional() -> None:
    from lsgc.broadcast.config import load_connected_channels, load_runtime_config

    assert load_connected_channels(environ={}) == ()
    config = load_runtime_config(environ={})
    assert config.channels == ()
