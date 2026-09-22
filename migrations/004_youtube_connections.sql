-- M12.5A durable YouTube channel connections.
-- Application accounts are not implemented yet; owner_user_id is reserved.
-- Token ciphertexts are credentials. They are never product API fields.

CREATE TABLE youtube_connections (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    youtube_channel_id TEXT NOT NULL,
    youtube_channel_title TEXT,
    status TEXT NOT NULL,
    granted_scopes TEXT,
    refresh_token_ciphertext TEXT,
    access_token_ciphertext TEXT,
    access_token_expires_at TIMESTAMPTZ,
    token_type TEXT,
    connected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_refreshed_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    disconnected_at TIMESTAMPTZ,
    owner_user_id UUID,
    CONSTRAINT youtube_connections_provider_youtube CHECK (provider = 'youtube'),
    CONSTRAINT youtube_connections_status_allowed CHECK (
        status IN ('CONNECTED', 'NEEDS_REAUTHORIZATION', 'DISCONNECTED')
    ),
    CONSTRAINT youtube_connections_channel_not_empty CHECK (youtube_channel_id <> '')
);

CREATE UNIQUE INDEX youtube_connections_active_channel
    ON youtube_connections (youtube_channel_id)
    WHERE status <> 'DISCONNECTED';

CREATE TABLE youtube_oauth_states (
    state TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    CONSTRAINT youtube_oauth_states_state_not_empty CHECK (state <> '')
);
