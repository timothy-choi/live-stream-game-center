-- Missed-scoring recovery provenance and history completeness.
-- Does not encode sport scoring rules. Does not rewrite historical gameplay.

ALTER TABLE confirmed_score_changes
    DROP CONSTRAINT IF EXISTS confirmed_score_changes_source_allowed;

ALTER TABLE confirmed_score_changes
    ADD CONSTRAINT confirmed_score_changes_source_allowed
    CHECK (source IN (
        'GAMEPLAY_SCOREBOARD',
        'RECOVERED_GAMEPLAY_SCOREBOARD',
        'POSTGAME_FINAL'
    ));

ALTER TABLE games
    ADD COLUMN recovery_status TEXT NOT NULL DEFAULT 'IDLE',
    ADD COLUMN score_history_completeness TEXT NOT NULL DEFAULT 'UNKNOWN';

ALTER TABLE games
    ADD CONSTRAINT games_recovery_status_allowed
    CHECK (recovery_status IN (
        'IDLE',
        'RECOVERING',
        'NOT_NEEDED',
        'COMPLETE',
        'PARTIAL',
        'UNAVAILABLE',
        'CONFLICT'
    ));

ALTER TABLE games
    ADD CONSTRAINT games_score_history_completeness_allowed
    CHECK (score_history_completeness IN (
        'UNKNOWN',
        'COMPLETE',
        'PARTIAL',
        'UNAVAILABLE'
    ));
