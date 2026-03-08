-- Add provider_url and model columns to condition_participants
-- Allows per-participant provider/model override (empty = use batch default)

ALTER TABLE condition_participants ADD COLUMN provider_url TEXT NOT NULL DEFAULT '';
ALTER TABLE condition_participants ADD COLUMN model TEXT NOT NULL DEFAULT '';
