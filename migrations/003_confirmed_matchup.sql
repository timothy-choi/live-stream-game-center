-- M10 confirmed visual matchup. Does not rewrite M8/M9.
-- Confirmed matchup = temporally confirmed visual scoreboard identity.
-- Not schedule truth, title-derived truth, owner-verified truth, or FINAL.

ALTER TABLE games
    ADD COLUMN confirmed_left_team_id TEXT,
    ADD COLUMN confirmed_right_team_id TEXT,
    ADD COLUMN matchup_revision INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN matchup_confirmed_at TIMESTAMPTZ,
    ADD COLUMN matchup_confirmed_timestamp_s DOUBLE PRECISION,
    ADD COLUMN matchup_confirmed_frame_index INTEGER,
    ADD COLUMN matchup_confirmed_epoch INTEGER,
    ADD COLUMN matchup_left_method TEXT,
    ADD COLUMN matchup_right_method TEXT,
    ADD COLUMN matchup_conflict_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN last_matchup_conflict TEXT;

ALTER TABLE games
    ADD CONSTRAINT games_matchup_revision_nonnegative
    CHECK (matchup_revision >= 0);

ALTER TABLE games
    ADD CONSTRAINT games_matchup_null_or_both
    CHECK (
        (
            matchup_revision = 0
            AND confirmed_left_team_id IS NULL
            AND confirmed_right_team_id IS NULL
        )
        OR (
            matchup_revision > 0
            AND confirmed_left_team_id IS NOT NULL
            AND confirmed_right_team_id IS NOT NULL
            AND confirmed_left_team_id <> confirmed_right_team_id
        )
    );

ALTER TABLE games
    ADD CONSTRAINT games_matchup_team_id_shape
    CHECK (
        (confirmed_left_team_id IS NULL OR confirmed_left_team_id ~ '^[A-Z]{2,3}$')
        AND (confirmed_right_team_id IS NULL OR confirmed_right_team_id ~ '^[A-Z]{2,3}$')
    );

CREATE TABLE confirmed_matchup_changes (
    id UUID PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES games (id),
    revision INTEGER NOT NULL,
    left_team_id TEXT NOT NULL,
    right_team_id TEXT NOT NULL,
    left_method TEXT NOT NULL,
    right_method TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    first_seen_timestamp_s DOUBLE PRECISION,
    first_seen_frame_index INTEGER,
    confirmed_timestamp_s DOUBLE PRECISION,
    confirmed_frame_index INTEGER,
    confirmed_epoch INTEGER,
    persisted_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT confirmed_matchup_game_revision_unique UNIQUE (game_id, revision),
    CONSTRAINT confirmed_matchup_revision_positive CHECK (revision >= 1),
    CONSTRAINT confirmed_matchup_evidence CHECK (evidence_count >= 2),
    CONSTRAINT confirmed_matchup_distinct CHECK (left_team_id <> right_team_id),
    CONSTRAINT confirmed_matchup_team_id_shape CHECK (
        left_team_id ~ '^[A-Z]{2,3}$' AND right_team_id ~ '^[A-Z]{2,3}$'
    )
);

CREATE INDEX confirmed_matchup_changes_game_revision_idx
    ON confirmed_matchup_changes (game_id, revision);
