# Design: make `tools_document` failures visible (issue #78)

_2026-09-22. Approved by the repo owner before implementation._

## Problem

In `consensus/tools_document/` the failure path is systematically
indistinguishable from the success path. An error becomes plausible-looking
content, is returned in a non-error `ToolResult` — which the UI renders with a
✅ (`static/discussion.js:210`) — is read by the AI as fact, and in one case is
written to the database permanently.

Issue #78 records ten instances. They were surfaced by the review of PR #77,
which split the former `tools_document.py` into a package; none is a regression
from that split, and the split deliberately left them untouched to preserve its
byte-identity safety argument. Golden rule 7 says discovered errors are fixed as
a priority rather than postponed.

## Approach

Errors are **typed at the fault site** and caught at the handler boundary. This
is the pattern issues #71–#74 established for the discussion-flow layer with
`models.ConfigurationError` and `ai_response.AIResponseFormatError`, and the
reasoning transfers directly: every one of these ten defects is a place where a
failure was made to *look like* a value, so making failure a distinct type is
the repair that cannot be quietly re-broken by the next edit.

Two alternatives were considered and rejected:

- **Result objects threaded through the call graph.** Equivalent expressiveness,
  but every intermediate function must remember to propagate, which is the same
  discipline that already failed here.
- **Status codes in `ToolResult.metadata`.** `metadata` is not shown to the
  model, so the AI would still read a failure as an answer.

## Decisions taken before implementation

| Question | Decision |
|---|---|
| How to record a failed summary (defect 1) | New `documents.summary_status` column, so the distinction survives a reload |
| `doc_get_chapter` scope (defect 10) | A chapter includes its subsections |
| How far an indexing failure surfaces (defect 3) | Error `ToolResult` **and** a transcript notice |

A summary **regeneration** tool is explicitly out of scope: the status column
makes retry possible, but a ninth `doc_*` tool is new feature work, not defect
repair. Recorded as a follow-up.

## Design

### 1. `tools_document/errors.py` (new)

```
DocumentError(Exception)            # package base
├── DocumentParseError              # bytes could not become usable text
├── DocumentInterpretationError     # an interpretation LLM call failed
└── DocumentIndexError              # chunks could not be embedded
```

Each carries an actionable `hint` alongside its message — "this looks like a
scanned or image-only PDF; OCR is required" rather than a bare traceback. The
handler layer is the only place these become `ToolResult(is_error=True)`; no
module below it converts an exception to prose.

### 2. `llm.py` raises instead of returning error strings — defects 1, 6

`_call_interpretation_llm` currently returns `"(Error: could not resolve caller
entity for LLM call)"` and `f"(LLM call failed: {e})"` **as the answer**. It
raises `DocumentInterpretationError` instead. Three consequences:

- `ingestion.py` catches it, calls `logger.exception`, and stores
  `summary=NULL` with `summary_status='failed'`. Its existing
  `except Exception -> summary = ""` is presently near-dead code precisely
  because the helper hands it a string rather than raising.
- `_doc_ask_handler` and `_doc_summary_handler` catch it and return
  `is_error=True`, naming the model, the provider and the document id.
- `_doc_add_handler`'s catch-all gains the `logger.exception` it never had, so
  a transport error, `HTTPStatusError`, PDF-library crash or SQLite write
  failure inside the ~80-line ingestion pipeline leaves a traceback somewhere.

### 3. Migration `015_document_summary_status.sql` — defect 1

```sql
ALTER TABLE documents ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'ok';
```

Values are `'ok'`, `'failed'` and `'pending'` (the latter for
`generate_summary=False`, or a call without the `context`/`app` that summary
generation silently requires). Existing rows default to `'ok'`: their summaries
cannot be retro-classified, and the ones written by the old error-string path
are at least no longer *added* to.

`db.add_document` takes the status; `get_document`, `get_all_documents` and
`get_discussion_documents` select it. `handlers._summary_snippet` becomes
status-aware, rendering `(summary unavailable — generation failed)` for
`'failed'` and `(no summary)` for `'pending'` instead of reprinting an LLM
error to every participant.

Consumers of the new column: `doc_list` renders it, and `doc_add`'s result
reports it. Per the contract recorded in HANDOVER — *every new result key has a
consumer* — no accessor is added that only tests would call.

### 4. `parsing.py` stops manufacturing content — defects 7, 8

`parse_document` returns a `ParsedDocument(markdown, fidelity, notes)` rather
than a bare `str`. Fidelity is `'full'` or `'degraded'`; `notes` carries the
human-readable reasons. This is a deliberate public-API change — the name is in
`__all__`, but its only non-test consumers are inside the package, so the blast
radius is the facade guard and the existing parsing tests.

- **Scanned PDFs.** `_parse_pdf`'s `"(Empty PDF)"` return is removed. Eleven
  non-blank characters passed `ingestion.py`'s `if not markdown.strip()` guard,
  so an image-only PDF ingested "successfully", was chunked and embedded, and
  `doc_ask` then answered questions "based ONLY on the provided passages" — from
  the string `(Empty PDF)`. Neither backend extracting text now raises
  `DocumentParseError`. Each fallback rung is logged, including pdfplumber
  succeeding but extracting nothing.
- **Paywalled / JS-rendered HTML.** When `trafilatura.extract` returns `None`
  the regex tag-stripper still runs, but the fallback is logged and the result
  is marked `degraded`, so the cookie banner and inlined `<script>` bodies are
  not passed off as the document.
- **Unrecognised MIME types.** A `.docx`, `.xlsx` or JPEG is currently
  `content.decode("utf-8", errors="replace")`d into mojibake and stored with a
  real character count. Binary content — NUL bytes, or a replacement-character
  ratio above `MAX_REPLACEMENT_CHAR_RATIO` — raises `DocumentParseError` naming
  what was detected.
- **`fetch_url_content`.** Gains retries with exponential backoff
  (`URL_FETCH_MAX_RETRIES`, `URL_FETCH_BASE_DELAY`) on timeouts, connect errors
  and 5xx, closing an open golden-rule-5 violation; 4xx raises immediately,
  since retrying a 404 is pointless. A `MAX_DOCUMENT_BYTES` cap is enforced on
  the `content-length` header *and* while streaming, so a header-less multi-GB
  URL cannot be read into memory.

All new tuning values go in `constants.py` (golden rule 3).

### 5. `consensus/background.py` (new) — defect 2

```python
def spawn_background(coro, description: str) -> asyncio.Task
```

Retains a strong reference — `asyncio` holds only a weak one, so an un-retained
task can be garbage-collected mid-run — **and** attaches a done-callback that
retrieves `task.exception()` and logs it with `description`. The existing
callback is a bare `_background_tasks.discard`, so the exception is never
retrieved and the failure appears only as asyncio's generic "Task exception was
never retrieved" at GC time, if at all.

The identical defect exists in `tools_memory.py:288`. Both modules import the
shared helper, so it is fixed once (golden rule 1).

`_embed_document_chunks` has a `finally` but no `except`: a
`sqlite3.OperationalError: database is locked` from `get_document_chunks`, or
anything raised inside its `except EmbeddingContextLengthError` block (a sibling
`except` cannot catch it), kills the pass with nothing logged. Its body is
wrapped in `except Exception: logger.exception(...)`.

### 6. Retrieval honesty — defects 4, 5

`_rank_by_similarity` returns a `RankingResult(ranked, skipped_dim_mismatch,
query_dim, row_dims)` carrying the ranked rows **and** the number of rows
skipped for dimension mismatch, with the dimensions involved. Counting in the
ranking function rather than logging inside `_cosine_similarity` avoids spamming
a log line per row, and keeps `_cosine_similarity` a pure function
(golden rule 1).

`_doc_ask_handler` passes `MIN_SIMILARITY_THRESHOLD`, as `_doc_list_handler`
already does at line 93. Today it uses the `threshold=0.0` default, so a
dimension mismatch scores every row 0.0, the stable sort returns the first five
rows in DB order, and the LLM is handed five arbitrary passages labelled
"relevance: 0.0" with instructions to answer only from them — a confident,
cited, wrong answer inside a ✅ tool call.

The two empty outcomes are then distinguished:

- Nothing above the floor, no mismatches → a plain non-error result: the
  document genuinely does not address the question.
- Nothing above the floor **and** mismatches counted → `is_error=True` naming
  both dimensions and stating that a re-index is required. This is the
  `nomic-embed-text` (768) → `mxbai-embed-large` (1024) switch, which currently
  makes the library look empty while the Documents panel still lists the files.

### 7. Indexing failure is stateful and reaches the transcript — defect 3

Today, when chunks are unembedded, `doc_ask` re-kicks the pass and returns
*"Document is still being indexed (0/N chunks embedded). Please try again
shortly."* as a **non-error**. If the embedding service is down or the model was
uninstalled, every call re-spawns a pass that fails every chunk and returns the
same encouraging message; the AI retries each turn up to `MAX_TOOL_ITERATIONS`,
burning tokens, and the user sees a discussion that silently never uses the
document.

Per-document failure state lives beside the existing `_embedding_docs` marker
set in `embedding.py`, as `_indexing_failures: dict[int, IndexingFailure]`
holding the consecutive-failure count, the last error and the last attempt
timestamp. `_embed_document_chunks` records an entry when a pass completes with
any failed chunk or dies outright, and deletes it after a fully clean pass.
`doc_ask` then branches:

- No recorded failure → the existing "still being indexed" message. A genuinely
  in-flight first pass is a true transient.
- A recorded failure → `is_error=True` carrying the embedder's real error, plus
  a transcript notice.

The notice goes through the existing `app_discussion_flow.helpers.post_notice`
contract. Handlers already receive `app`, so no change to `ToolContext` or the
registry is needed; the call is guarded so that a missing discussion, or a
notice failure, cannot break the tool call that reported the problem. One notice
per document per failure streak, so `MAX_TOOL_ITERATIONS` retries cannot spam
the transcript.

This introduces a `tools_document -> app_discussion_flow` import. It is
one-directional and lazy (inside the function body, as the package's other
cross-module imports already are), and `app.py` wires both.

### 8. Ranges and chapters — defects 9, 10

New pure module `tools_document/validation.py`:

- `resolve_range(from_char, to_char, length)` — raises on `from > to` and on
  negatives other than the documented `-1` sentinel, and clamps beyond-length.
  Used by **both** `_doc_get_text_handler` and `_doc_summary_handler`; only the
  latter guards today, and the inconsistency looks accidental. Currently
  `markdown[500:100]` returns `""` in a non-error result with `"length": 0`, and
  `to_char=-5` silently drops the last five characters via negative slicing.
- `chapter_range(sections, index)` — scans forward for the next header at the
  same or a higher level, so `## Methods` carries its `### Participants` and
  `### Procedure`. `extract_sections` and the stored `sections_json` are
  untouched, so chunk boundaries are unaffected and no re-ingest is needed.
  Handler metadata reports `subsections_included`; the tool description in
  `provider.py` is corrected to match the behaviour.

## Testing

TDD: a failing test precedes each repair. Real `Database` via the `tmp_db`
fixture, with fakes only at the network edges, extending
`tests/document_helpers.py` — mock one layer further out than the code under
test, per the lesson recorded in HANDOVER from the unreachable-#72 defect.

New: `tests/test_tools_document_failures.py` and `tests/test_background.py`.
Updated: the parsing tests and `tests/test_tools_document_facade.py` for
`parse_document`'s new return type. A migration test covers the new column on
both a fresh database and one created before migration 015.

## Module sizes (golden rule 8, issue #61)

`handlers.py` is at 470 lines and this work pushes it over the limit, so the RAG
handlers (`doc_ask`, `doc_summary`) move to their own module at the moment the
limit is crossed rather than later. New modules `errors.py` and `validation.py`
are small and single-purpose by construction.

## Scope note

This is a large single PR: roughly six changed modules, four new ones, a
migration and 60+ tests. A natural split point is after §5 — typed errors,
parsing and background tasks — with §6–§8 following separately. The repo owner
chose to take it as one PR.

## Follow-ups not taken here

- A summary **regeneration** path (tool or UI action) now that
  `summary_status='failed'` is recordable.
- OCR for scanned PDFs, which §4 now names as the remedy but does not provide.
