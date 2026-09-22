-- Summary generation outcome (issue #78 defect 1): a failed interpretation
-- call used to be persisted as the summary text itself. The summary column
-- now holds only real summaries; this column says why one is missing.
--   'ok'      summary generated, or the row predates this column
--   'failed'  generation was attempted and raised
--   'pending' generation was not attempted (no context/app, or opted out)
ALTER TABLE documents ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'ok';
