-- Extraction fidelity (issue #81): parse_document marks a regex tag-strip
-- fallback as 'degraded' with notes saying why, but that reached only the
-- upload toast. Persisting it lets doc_list, doc_ask, doc_summary and the
-- Documents panel warn on every later read.
--   fidelity     'full' (a real extractor produced the text, or the row
--                predates this column) or 'degraded'
--   parse_notes  JSON list of strings explaining a degraded extraction
ALTER TABLE documents ADD COLUMN fidelity TEXT NOT NULL DEFAULT 'full';
ALTER TABLE documents ADD COLUMN parse_notes TEXT NOT NULL DEFAULT '[]';
