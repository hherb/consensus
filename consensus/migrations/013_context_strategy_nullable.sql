-- Make the per-member context-strategy override columns nullable.
--
-- Migration 011 added discussion_members.context_strategy /
-- context_window_size as NOT NULL with defaults ('sliding_window' / 20).
-- Because the columns can never be NULL, ContextConfig.from_member_row
-- always reads a member value and the discussion-level default
-- (default_context_strategy / default_context_window_size) is never
-- applied.  Recreate the table with the override columns nullable
-- (DEFAULT NULL) so "member has not overridden" is representable, and
-- convert existing default-valued rows back to NULL so they inherit the
-- discussion-level default.  Genuine non-default overrides are preserved.

-- SQLite does not support ALTER COLUMN, so we recreate the table.
-- No tables reference discussion_members, so DROP is safe; disable
-- foreign keys during the rebuild to be consistent with migration 004.
PRAGMA foreign_keys=OFF;

CREATE TABLE discussion_members_new (
    discussion_id       INTEGER NOT NULL,
    entity_id           INTEGER NOT NULL,
    is_moderator        INTEGER NOT NULL DEFAULT 0,
    also_participant    INTEGER NOT NULL DEFAULT 0,
    turn_position       INTEGER,
    participant_role    TEXT NOT NULL DEFAULT 'standard',
    context_strategy    TEXT DEFAULT NULL,
    context_window_size INTEGER DEFAULT NULL,
    PRIMARY KEY (discussion_id, entity_id),
    FOREIGN KEY (discussion_id) REFERENCES discussions(id),
    FOREIGN KEY (entity_id)     REFERENCES entities(id)
);

INSERT INTO discussion_members_new
    (discussion_id, entity_id, is_moderator, also_participant,
     turn_position, participant_role, context_strategy, context_window_size)
SELECT
    discussion_id, entity_id, is_moderator, also_participant,
    turn_position, participant_role,
    NULLIF(context_strategy, 'sliding_window'),
    NULLIF(context_window_size, 20)
FROM discussion_members;

DROP TABLE discussion_members;
ALTER TABLE discussion_members_new RENAME TO discussion_members;

PRAGMA foreign_keys=ON;
