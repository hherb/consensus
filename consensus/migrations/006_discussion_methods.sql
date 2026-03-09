-- Add discussion method support
ALTER TABLE discussions ADD COLUMN discussion_method TEXT NOT NULL DEFAULT 'open_discussion';
ALTER TABLE discussions ADD COLUMN method_state TEXT DEFAULT '{}';
