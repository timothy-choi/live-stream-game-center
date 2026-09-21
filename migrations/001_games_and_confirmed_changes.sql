-- M8 durable game identity + trusted visual score + append-only history.
-- Trusted score = temporally confirmed visual score. Not official / final.

CREATE TABLE games (
    id UUID PRIMARY KEY,
    platform TEXT NOT NULL,
    external_broadcast_id TEXT NOT NULL,
    channel_id TEXT,
    tracking_status TEXT NOT NULL,
    trusted_left_score INTEGER,
    trusted_right_score INTEGER,
    score_revision INTEGER NOT NULL,
    last_confirmed_at TIMESTAMPTZ,
    last_confirmed_timestamp_s DOUBLE PRECISION,
    last_confirmed_frame_index INTEGER,
    last_confirmed_epoch INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT games_platform_broadcast_unique UNIQUE (platform, external_broadcast_id),
    CONSTRAINT games_revision_nonnegative CHECK (score_revision >= 0),
    CONSTRAINT games_status_allowed CHECK (
        tracking_status IN ('TRACKING', 'ACQUISITION_ENDED')
    ),
    CONSTRAINT games_scores_match_revision CHECK (
        (
            score_revision = 0
            AND trusted_left_score IS NULL
            AND trusted_right_score IS NULL
        )
        OR (
            score_revision > 0
            AND trusted_left_score IS NOT NULL
            AND trusted_right_score IS NOT NULL
            AND trusted_left_score >= 0
            AND trusted_right_score >= 0
        )
    ),
    CONSTRAINT games_platform_not_empty CHECK (platform <> ''),
    CONSTRAINT games_broadcast_not_empty CHECK (external_broadcast_id <> '')
);

CREATE INDEX games_status_idx ON games (tracking_status);

CREATE TABLE confirmed_score_changes (
    id UUID PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES games (id),
    revision INTEGER NOT NULL,
    previous_left INTEGER,
    previous_right INTEGER,
    new_left INTEGER NOT NULL,
    new_right INTEGER NOT NULL,
    initialization BOOLEAN NOT NULL,
    evidence_count INTEGER NOT NULL,
    first_seen_timestamp_s DOUBLE PRECISION,
    first_seen_frame_index INTEGER,
    confirmed_timestamp_s DOUBLE PRECISION,
    confirmed_frame_index INTEGER,
    confirmed_epoch INTEGER,
    persisted_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT confirmed_score_changes_game_revision_unique UNIQUE (game_id, revision),
    CONSTRAINT confirmed_score_changes_revision_positive CHECK (revision >= 1),
    CONSTRAINT confirmed_score_changes_evidence CHECK (evidence_count >= 2),
    CONSTRAINT confirmed_score_changes_new_scores CHECK (
        new_left >= 0 AND new_right >= 0
    ),
    CONSTRAINT confirmed_score_changes_init_previous CHECK (
        (
            initialization
            AND previous_left IS NULL
            AND previous_right IS NULL
        )
        OR (
            NOT initialization
            AND previous_left IS NOT NULL
            AND previous_right IS NOT NULL
            AND previous_left >= 0
            AND previous_right >= 0
        )
    )
);

CREATE INDEX confirmed_score_changes_game_revision_idx
    ON confirmed_score_changes (game_id, revision);
