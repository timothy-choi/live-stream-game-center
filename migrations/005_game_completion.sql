-- M12.5B verified game completion is distinct from YouTube ENDED.
-- Does not add team identity. Does not rewrite historical scores.

ALTER TABLE games
    ADD COLUMN completion_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    ADD COLUMN completion_confirmed_at TIMESTAMPTZ,
    ADD COLUMN completion_confirmed_timestamp_s DOUBLE PRECISION,
    ADD COLUMN completion_confirmed_frame_index INTEGER,
    ADD COLUMN finalizing_started_at TIMESTAMPTZ,
    ADD COLUMN finalizing_deadline_at TIMESTAMPTZ,
    ADD COLUMN postgame_left_score INTEGER,
    ADD COLUMN postgame_right_score INTEGER,
    ADD COLUMN postgame_confirmed_at TIMESTAMPTZ,
    ADD COLUMN postgame_confirmed_timestamp_s DOUBLE PRECISION,
    ADD COLUMN final_score_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN final_score_conflict TEXT,
    ADD COLUMN score_stable_since_at TIMESTAMPTZ,
    ADD COLUMN score_stable_since_timestamp_s DOUBLE PRECISION;

ALTER TABLE games
    ADD CONSTRAINT games_completion_status_allowed
    CHECK (completion_status IN ('UNVERIFIED', 'FINALIZING', 'FINAL'));

ALTER TABLE games
    ADD CONSTRAINT games_final_requires_verified_postgame
    CHECK (
        completion_status <> 'FINAL'
        OR (
            final_score_verified
            AND postgame_left_score IS NOT NULL
            AND postgame_right_score IS NOT NULL
            AND trusted_left_score IS NOT DISTINCT FROM postgame_left_score
            AND trusted_right_score IS NOT DISTINCT FROM postgame_right_score
            AND final_score_conflict IS NULL
        )
    );

ALTER TABLE confirmed_score_changes
    ADD COLUMN source TEXT NOT NULL DEFAULT 'GAMEPLAY_SCOREBOARD';

ALTER TABLE confirmed_score_changes
    ADD CONSTRAINT confirmed_score_changes_source_allowed
    CHECK (source IN ('GAMEPLAY_SCOREBOARD', 'POSTGAME_FINAL'));
