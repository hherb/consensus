-- Flag the pre-#78 scanned-PDF rows (issue #80, option 1).
--
-- Before #78, a PDF with no extractable text was stored with the literal
-- markdown '(Empty PDF)', chunked and embedded, so doc_ask answered from
-- that string. Marking the rows degraded makes every reader warn; the text
-- and chunks are left alone, because deleting a user's stored content is
-- not a decision for a migration to make silently.
--
-- Its own version, not part of 017, so it can be re-applied in a test.
UPDATE documents
   SET fidelity = 'degraded',
       parse_notes = '["No text could be extracted (likely a scanned PDF); this document was stored before such files were rejected. Re-add it after running OCR."]'
 WHERE markdown = '(Empty PDF)';
