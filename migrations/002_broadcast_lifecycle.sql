-- M9 YouTube platform lifecycle + schedule metadata + tracking-enabled.
-- Does not rewrite M8. Does not add FINAL/winner columns.

ALTER TABLE games
    ADD COLUMN title TEXT,
    ADD COLUMN platform_lifecycle TEXT NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN scheduled_start_at TIMESTAMPTZ,
    ADD COLUMN actual_start_at TIMESTAMPTZ,
    ADD COLUMN actual_end_at TIMESTAMPTZ,
    ADD COLUMN last_synced_at TIMESTAMPTZ,
    ADD COLUMN tracking_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN last_tracking_error TEXT,
    ADD COLUMN last_worker_finished_at TIMESTAMPTZ;

ALTER TABLE games
    ADD CONSTRAINT games_platform_lifecycle_allowed
    CHECK (platform_lifecycle IN ('UNKNOWN', 'UPCOMING', 'LIVE', 'ENDED'));

CREATE INDEX games_channel_lifecycle_idx
    ON games (channel_id, platform_lifecycle);

CREATE INDEX games_channel_scheduled_idx
    ON games (channel_id, scheduled_start_at);
