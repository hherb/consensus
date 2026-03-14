-- Per-entity context strategy for each discussion membership
ALTER TABLE discussion_members ADD COLUMN context_strategy TEXT NOT NULL DEFAULT 'sliding_window';
ALTER TABLE discussion_members ADD COLUMN context_window_size INTEGER NOT NULL DEFAULT 20;

-- Discussion-level defaults (applied when member doesn't override)
ALTER TABLE discussions ADD COLUMN default_context_strategy TEXT NOT NULL DEFAULT 'sliding_window';
ALTER TABLE discussions ADD COLUMN default_context_window_size INTEGER NOT NULL DEFAULT 20;
