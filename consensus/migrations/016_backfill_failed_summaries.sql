-- Retro-classify the summaries the pre-#78 interpretation helper stored as
-- text (issue #78 whole-branch review).
--
-- 015 added summary_status with DEFAULT 'ok', which marked every existing
-- row as having a good summary -- including the rows that *are* defect 1,
-- whose summary column literally holds an LLM error. Those kept printing to
-- every participant on every doc_list, which is the exact symptom #78
-- describes.
--
-- The old helper produced exactly two strings, so they classify precisely:
--   return "(Error: could not resolve caller entity for LLM call)"
--   return f"(LLM call failed: {e})"
-- The design note's "their summaries cannot be retro-classified" was wrong;
-- a two-clause prefix match is enough.
--
-- Applied as its own version rather than by editing 015, because 015 may
-- already be recorded as applied on a database that ran this branch.
UPDATE documents
   SET summary = '',
       summary_status = 'failed'
 WHERE summary LIKE '(LLM call failed:%'
    OR summary = '(Error: could not resolve caller entity for LLM call)';
