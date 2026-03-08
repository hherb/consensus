-- Add max_rounds column to discussions (0 = unlimited)
ALTER TABLE discussions ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT 0;
