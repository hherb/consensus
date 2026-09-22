# tools_document Failure Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every failure path in `consensus/tools_document/` distinguishable from a success, so an error can no longer be read by the AI as fact or persisted to the database as content (issue #78).

**Architecture:** Errors are typed at the fault site — a new `tools_document/errors.py` exception family — and converted to `ToolResult(is_error=True)` only at the handler boundary. This mirrors what issues #71–#74 established for the discussion-flow layer with `models.ConfigurationError` and `ai_response.AIResponseFormatError`. Supporting changes: a `documents.summary_status` column so a failed summary survives a reload, a shared background-task helper that actually retrieves task exceptions, a relevance floor on RAG retrieval, and pure range/chapter validation helpers.

**Tech Stack:** Python 3.11+, asyncio, httpx, SQLite, pytest. Package management is `uv` only — never `pip`.

**Spec:** `docs/superpowers/specs/2026-09-22-tools-document-failure-visibility-design.md`

## Global Constraints

- **`uv` only.** Run the suite with `uv run pytest`. Never invoke `pip` or create a venv by hand.
- **TDD.** Every task writes a failing test first and runs it to confirm it fails for the expected reason before any implementation.
- **Docstrings and type hints are mandatory** on every function and class added or touched (golden rule 2).
- **No magic numbers.** Every tuning value lives in `consensus/tools_document/constants.py` (golden rule 3).
- **Network functions retry with exponential backoff** up to a `MAX_RETRIES` constant (golden rule 5).
- **Every caught error is shown in the UI and logged** (golden rule 6).
- **Files stay under ~500 lines** (golden rule 8, issue #61). `handlers.py` is at 470 — Task 12 splits it at the moment this work crosses the limit.
- **Mock one layer further out than the code under test.** Real `Database` via the `tmp_db` fixture from `tests/conftest.py`; fakes only at network edges, in `tests/document_helpers.py`. A test that mocks the thing it is meant to be testing proves nothing (the lesson recorded in HANDOVER from the unreachable-#72 defect).
- **Patch where a name is defined,** not on the package facade — use `tests/document_helpers.py::patch_where_defined`. `from x import y` copies the reference, so patching `consensus.tools_document.<name>` does not intercept the binding `handlers.py` actually calls.
- **Every new result key needs a production consumer.** A key only tests read is a contract nobody honours.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `consensus/background.py` | `spawn_background()` — fire-and-forget task that retains a strong reference and logs its exception |
| `consensus/tools_document/errors.py` | The `DocumentError` exception family |
| `consensus/tools_document/validation.py` | Pure `resolve_range()` and `chapter_range()` |
| `consensus/tools_document/handlers_rag.py` | `doc_ask` and `doc_summary` handlers (Task 12) |
| `consensus/migrations/015_document_summary_status.sql` | Adds `documents.summary_status` |
| `tests/test_background.py` | Covers the shared background helper |
| `tests/test_tools_document_failures.py` | Covers every repair in this plan |

**Modified:** `tools_document/{constants,llm,ingestion,parsing,embedding,handlers,provider,__init__}.py`, `consensus/tools_memory.py`, `consensus/db/documents.py`, `tests/document_helpers.py`, `tests/test_tools_document*.py`, plus docs in Task 12.

---

### Task 1: Shared background-task helper

The existing `_spawn_background` in both `tools_document/embedding.py:63` and `tools_memory.py:288` attaches a bare `_background_tasks.discard` done-callback. It never calls `task.exception()`, so a crash in a background pass surfaces only as asyncio's generic "Task exception was never retrieved" at garbage-collection time, if at all.

**Files:**
- Create: `consensus/background.py`
- Create: `tests/test_background.py`
- Modify: `consensus/tools_document/embedding.py:58-67`, `consensus/tools_memory.py:283-292`

**Interfaces:**
- Produces: `consensus.background.spawn_background(coro, description: str) -> asyncio.Task`

- [ ] **Step 1: Write the failing test**

Create `tests/test_background.py`:

```python
"""Tests for the shared fire-and-forget background task helper."""

import asyncio
import logging

import pytest

from consensus.background import spawn_background


@pytest.mark.asyncio
async def test_exception_is_retrieved_and_logged(caplog):
    """A crashing background task logs its traceback rather than vanishing.

    Regression test for issue #78 defect 2: the previous done-callback never
    called ``task.exception()``, so the failure was invisible until GC.
    """
    async def boom():
        raise RuntimeError("embedding pass died")

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(boom(), "embed doc 7")
        await asyncio.gather(task, return_exceptions=True)

    assert "embed doc 7" in caplog.text
    assert "embedding pass died" in caplog.text
    assert task.exception() is not None


@pytest.mark.asyncio
async def test_successful_task_logs_nothing_and_is_released(caplog):
    """A clean task leaves no error log and drops out of the registry."""
    from consensus import background

    async def fine():
        return 42

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(fine(), "harmless")
        await task

    assert caplog.text == ""
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in background._background_tasks


@pytest.mark.asyncio
async def test_task_is_strongly_referenced_while_running():
    """The helper retains a reference so the task cannot be GC'd mid-run."""
    from consensus import background

    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.01)

    task = spawn_background(slow(), "slow one")
    await started.wait()
    assert task in background._background_tasks
    await task


@pytest.mark.asyncio
async def test_cancellation_is_not_logged_as_an_error(caplog):
    """A cancelled task is a deliberate act, not a failure to report."""
    async def forever():
        await asyncio.sleep(3600)

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(forever(), "cancelled one")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert caplog.text == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_background.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.background'`

- [ ] **Step 3: Write the implementation**

Create `consensus/background.py`:

```python
"""Fire-and-forget asyncio task scheduling with visible failures.

``asyncio`` holds only a weak reference to a task, so an un-retained
background task can be garbage-collected mid-run; and a task whose
exception is never retrieved reports its failure only as asyncio's generic
"Task exception was never retrieved" warning at GC time — which is to say,
usually not at all.  Both traps are easy to reproduce and were present in
two copies of the same helper (issue #78 defect 2), so the fix lives here
once (golden rule 1).
"""

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks, discarded when each completes.
_background_tasks: set[asyncio.Task] = set()


def _log_task_outcome(task: asyncio.Task, description: str) -> None:
    """Retrieve a finished task's exception and log it.

    Retrieval is the point: leaving the exception unretrieved is what makes
    the failure invisible.  Cancellation is deliberate and is not reported
    as an error (golden rule 6 is about *caught errors*, not about noise).
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task failed (%s): %s", description, exc,
            exc_info=exc,
        )


def spawn_background(
    coro: Coroutine[Any, Any, Any], description: str,
) -> asyncio.Task:
    """Schedule *coro* as a background task whose failure will be logged.

    Args:
        coro: The coroutine to run detached from the caller.
        description: Short human-readable label naming the work, used in
            the log line when the task fails (e.g. ``"embed doc 7"``).

    Returns:
        The scheduled task.  Callers may ignore it; the helper keeps its
        own strong reference until it completes.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(lambda t: _log_task_outcome(t, description))
    return task
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_background.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Adopt the helper in both existing call sites**

In `consensus/tools_document/embedding.py`, delete the local `_background_tasks` set and `_spawn_background` function (lines 58-67) and add near the other imports:

```python
from ..background import spawn_background
```

Replace the two call sites — `ingestion.py:115` and `handlers.py:332` — which currently read `_spawn_background(_embed_document_chunks(doc_id, db, embed_client))`. Keep a thin module-level wrapper in `embedding.py` so existing imports of `_spawn_background` keep resolving and the description is supplied in one place:

```python
def _spawn_embedding_pass(doc_id: int, db, embed_client) -> None:
    """Schedule the background embedding pass for one document."""
    spawn_background(
        _embed_document_chunks(doc_id, db, embed_client),
        f"embed document {doc_id}",
    )
```

Update `ingestion.py` and `handlers.py` to import and call `_spawn_embedding_pass(doc_id, db, embed_client)` instead of `_spawn_background(...)`.

In `consensus/tools_memory.py`, delete `_background_tasks` and `_spawn_background` (lines 283-292), add `from .background import spawn_background`, and update its call site (the discussion-indexing spawn near line 288's usage) to pass a description such as `f"index discussion {discussion_id}"`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: 2816 + 4 = 2820 passed. If anything fails, it is a test importing `_spawn_background` by name — repoint it at `_spawn_embedding_pass` or `spawn_background`.

- [ ] **Step 7: Commit**

```bash
git add consensus/background.py tests/test_background.py \
        consensus/tools_document/embedding.py \
        consensus/tools_document/ingestion.py \
        consensus/tools_document/handlers.py \
        consensus/tools_memory.py
git commit -m "$(cat <<'EOF'
fix(#78): retrieve and log background task exceptions

Both tools_document and tools_memory had a copy of a spawn helper whose
done-callback only discarded the task reference. The exception was never
retrieved, so a background embedding or indexing pass could die with
nothing logged beyond asyncio's GC-time warning.

One shared consensus/background.py now retains the strong reference and
logs the outcome; cancellation stays silent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Typed errors; the interpretation LLM raises

`llm.py:23` and `:46` return `"(Error: could not resolve caller entity for LLM call)"` and `f"(LLM call failed: {e})"` **as the answer**. `_call_interpretation_llm` never raises, so `ingestion.py`'s `except Exception -> summary = ""` is near-dead code and `doc_ask` returns an expired-API-key message as the document's answer in a non-error result.

**Files:**
- Create: `consensus/tools_document/errors.py`
- Create: `tests/test_tools_document_failures.py`
- Modify: `consensus/tools_document/llm.py`, `consensus/tools_document/ingestion.py:65-81`, `consensus/tools_document/handlers.py:81-82`

**Interfaces:**
- Produces: `errors.DocumentError`, `errors.DocumentParseError`, `errors.DocumentInterpretationError`, `errors.DocumentIndexError`, each `__init__(message: str, hint: str = "")` exposing `.hint`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_document_failures.py`:

```python
"""Failure-path tests for the document RAG package (issue #78).

Every test here pins a case where a failure used to be indistinguishable
from a success: an error string returned as content, a non-error result,
or a value persisted to the database.
"""

import logging

import pytest

from consensus.tools_document import ingestion, llm
from consensus.tools_document.errors import (
    DocumentError, DocumentInterpretationError,
)
from consensus.tools import ToolContext
from tests.document_helpers import patch_where_defined


class FailingApp:
    """Minimal ``app`` stand-in whose entity lookup succeeds."""

    def __init__(self, db, entity_row):
        self.db = db
        self._entity_row = entity_row

    def _resolve_key_for_moderator(self, provider_id, env_name):
        return "test-key"


def test_interpretation_error_is_a_document_error():
    """The family shares a base so handlers can catch one type."""
    err = DocumentInterpretationError("boom", hint="check the API key")
    assert isinstance(err, DocumentError)
    assert err.hint == "check the API key"
    assert "check the API key" in str(err)


@pytest.mark.asyncio
async def test_unresolvable_caller_entity_raises(tmp_db):
    """An unknown caller entity raises instead of returning prose.

    Previously returned the string "(Error: could not resolve caller
    entity for LLM call)" as the answer.
    """
    class App:
        db = tmp_db

    context = ToolContext(caller_entity_id=9999, discussion_id=0)
    with pytest.raises(DocumentInterpretationError) as exc:
        await llm._call_interpretation_llm(
            App(), context, system_prompt="s", user_prompt="u",
        )
    assert "9999" in str(exc.value)


@pytest.mark.asyncio
async def test_completion_failure_raises(tmp_db, sample_ai_entity, monkeypatch):
    """A failing completion call raises rather than returning its message."""
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def complete(self, **kwargs):
            raise RuntimeError("401 Unauthorized")

        async def close(self):
            return None

    monkeypatch.setattr(llm, "AIClient", FakeClient)

    class App:
        db = tmp_db

        def _resolve_key_for_moderator(self, provider_id, env_name):
            return "k"

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    with pytest.raises(DocumentInterpretationError) as exc:
        await llm._call_interpretation_llm(
            App(), context, system_prompt="s", user_prompt="u",
        )
    assert "401 Unauthorized" in str(exc.value)


@pytest.mark.asyncio
async def test_failed_summary_is_not_persisted(
    tmp_db, sample_ai_entity, monkeypatch, caplog,
):
    """A failed summary never reaches documents.summary.

    Before this fix the LLM error string was stored and then reprinted to
    every participant by doc_list, permanently.
    """
    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("401 Unauthorized")

    patch_where_defined(
        monkeypatch, ingestion.ingest_document,
        "_call_interpretation_llm", boom,
    )

    class App:
        db = tmp_db

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    with caplog.at_level(logging.ERROR):
        result = await ingestion.ingest_document(
            app=App(), db=tmp_db, embed_client=None,
            content_bytes=b"# Title\n\nSome body text.",
            filename="doc.md", mime_type="text/markdown",
            context=context,
        )

    stored = tmp_db.get_document(result["document_id"])
    assert not stored["summary"]
    assert "401 Unauthorized" not in (stored["summary"] or "")
    assert "401 Unauthorized" in caplog.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.tools_document.errors'`

- [ ] **Step 3: Create the error family**

Create `consensus/tools_document/errors.py`:

```python
"""Typed failures for the document RAG pipeline.

Every defect recorded in issue #78 shares one shape: a failure was made to
look like a value — an error string returned as an answer, pseudo-content
manufactured from a failed extraction, a non-error ``ToolResult``.  Typing
the fault where it happens is what makes that impossible to reintroduce;
the handler layer is the only place these become
``ToolResult(is_error=True)``.

The pattern matches ``models.ConfigurationError`` and
``ai_response.AIResponseFormatError`` in the discussion-flow layer
(issues #71-#74).
"""


class DocumentError(Exception):
    """Base for every failure raised inside ``tools_document``.

    Args:
        message: What went wrong, in terms the caller can report.
        hint: An actionable remedy, if one is known — "this looks like a
            scanned PDF; OCR is required" is worth more to a user than a
            traceback.  Appended to ``str()`` when present.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message} ({self.hint})"
        return self.message


class DocumentParseError(DocumentError):
    """Raised when document bytes could not be turned into usable text."""


class DocumentInterpretationError(DocumentError):
    """Raised when an interpretation LLM call could not produce an answer."""


class DocumentIndexError(DocumentError):
    """Raised when a document's chunks could not be embedded."""
```

- [ ] **Step 4: Make `llm.py` raise**

Replace the body of `_call_interpretation_llm` in `consensus/tools_document/llm.py`. Add `from .errors import DocumentInterpretationError` to the imports, and rewrite:

```python
async def _call_interpretation_llm(
    app, context: ToolContext,
    system_prompt: str, user_prompt: str,
) -> str:
    """Call an LLM for document interpretation using the caller's config.

    Raises:
        DocumentInterpretationError: If the caller entity cannot be
            resolved, or the completion call fails.  It raises rather than
            returning a parenthesised error string, because callers used
            the return value verbatim: it became ``doc_ask``'s answer and,
            via ``ingest_document``, the document's persisted ``summary``
            (issue #78 defect 1).
    """
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        raise DocumentInterpretationError(
            f"Could not resolve caller entity {context.caller_entity_id} "
            "for the document interpretation call",
            hint="the entity may have been removed from the discussion",
        )

    ai_config = AIConfig.from_db_row(entity)
    api_key = app._resolve_key_for_moderator(
        ai_config.provider_id, entity.get("api_key_env", ""),
    )

    client = AIClient(
        base_url=ai_config.base_url, api_key=api_key, timeout=LLM_TIMEOUT,
    )
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await client.complete(
            messages=messages,
            model=ai_config.model,
            temperature=INTERPRETATION_TEMPERATURE,
            max_tokens=ai_config.max_tokens,
        )
        return response.content
    except Exception as e:
        logger.warning("Interpretation LLM call failed: %s", e)
        raise DocumentInterpretationError(
            f"The interpretation model {ai_config.model} failed: {e}",
            hint="check the provider's API key, quota and base URL",
        ) from e
    finally:
        await client.close()
```

- [ ] **Step 5: Make `ingestion.py` handle the raise**

Replace lines 65-81 of `consensus/tools_document/ingestion.py`:

```python
    # Generate summary.  A failed summary must never be persisted: the old
    # helper returned its error as a string, which was stored and then
    # reprinted to every participant by doc_list forever (issue #78).
    summary = ""
    if generate_summary and context and app:
        try:
            excerpt = markdown[:SUMMARY_EXCERPT_CHARS]
            summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "You are a document analyst. Provide a brief summary "
                    "(2-3 sentences) of the following document excerpt. "
                    "Focus on the main topic, key findings or arguments."
                ),
                user_prompt=excerpt,
            )
        except DocumentInterpretationError:
            logger.exception(
                "Summary generation failed for %s — storing no summary",
                filename,
            )
            summary = ""
```

Add `from .errors import DocumentInterpretationError` to its imports.

- [ ] **Step 6: Give `_doc_add_handler` its missing log**

In `consensus/tools_document/handlers.py`, the catch-all at line 81 returns an error with no log, pre-empting `tools.py:350`'s `logger.exception` and leaving no traceback anywhere. Replace:

```python
    except DocumentError as e:
        logger.warning("doc_add failed for %s: %s", url or filename, e)
        return ToolResult(content=f"Failed to add document: {e}", is_error=True)
    except Exception as e:
        logger.exception("doc_add failed unexpectedly for %s", url or filename)
        return ToolResult(content=f"Failed to add document: {e}", is_error=True)
```

Add `from .errors import DocumentError` to its imports. The split matters: an expected `DocumentError` is a reportable condition, anything else is a bug and deserves the traceback.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py tests/test_tools_document_pipeline.py -v`
Expected: the new tests PASS. Existing pipeline tests asserting on `"(LLM call failed:"` strings will fail — update them to assert the raise, since they were pinning the defect.

- [ ] **Step 8: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/tools_document/errors.py \
        consensus/tools_document/llm.py \
        consensus/tools_document/ingestion.py \
        consensus/tools_document/handlers.py \
        tests/test_tools_document_failures.py \
        tests/test_tools_document_pipeline.py
git commit -m "$(cat <<'EOF'
fix(#78): raise typed errors from the interpretation LLM helper

_call_interpretation_llm returned its failures as parenthesised strings
that callers used verbatim: an expired API key became doc_ask's answer
and, through ingest_document, was written to documents.summary and
reprinted to every participant by doc_list forever.

It now raises DocumentInterpretationError. Ingestion stores no summary
and logs the traceback; doc_add distinguishes a reportable DocumentError
from a bug worth a traceback.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Migration 015 and the `summary_status` column

**Files:**
- Create: `consensus/migrations/015_document_summary_status.sql`
- Modify: `consensus/db/documents.py:19-40` (`add_document`), `:42-57` (`get_document`), `:79-94` (`get_all_documents`), `:113-128` (`get_discussion_documents`)
- Modify: `consensus/tools_document/constants.py`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `db.add_document(..., summary_status: str = "ok") -> int`; every document row dict gains a `summary_status` key. Constants `SUMMARY_STATUS_OK = "ok"`, `SUMMARY_STATUS_FAILED = "failed"`, `SUMMARY_STATUS_PENDING = "pending"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
def test_migration_adds_summary_status_column(tmp_db):
    """A freshly created database carries the new column."""
    cols = {
        row[1] for row in
        tmp_db.conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    assert "summary_status" in cols


def test_existing_rows_default_to_ok(tmp_db):
    """Rows written without an explicit status read back as 'ok'.

    Pre-015 rows cannot be retro-classified, so the migration's DEFAULT
    keeps them readable rather than NULL.
    """
    tmp_db.conn.execute(
        "INSERT INTO documents (filename, title, summary, mime_type, "
        "source_type, source_url, markdown, char_count, sections_json, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a.md", "A", "s", "text/markdown", "upload", None, "# A", 3,
         "[]", 0.0),
    )
    tmp_db.conn.commit()
    doc_id = tmp_db.conn.execute(
        "SELECT id FROM documents WHERE filename='a.md'"
    ).fetchone()[0]
    assert tmp_db.get_document(doc_id)["summary_status"] == "ok"


def test_add_document_records_a_failed_status(tmp_db):
    """The status round-trips through add_document and every read path."""
    doc_id = tmp_db.add_document(
        filename="b.md", title="B", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# B",
        char_count=3, sections_json="[]", summary_status="failed",
    )
    assert tmp_db.get_document(doc_id)["summary_status"] == "failed"
    all_docs = {d["id"]: d for d in tmp_db.get_all_documents()}
    assert all_docs[doc_id]["summary_status"] == "failed"

    disc_id = tmp_db.create_discussion("T", "topic", 0)
    tmp_db.add_discussion_document(disc_id, doc_id)
    attached = tmp_db.get_discussion_documents(disc_id)
    assert attached[0]["summary_status"] == "failed"
```

> If `create_discussion`'s signature differs, read `consensus/db/discussions.py` and use the real one — the point of the assertion is the joined read path, not the discussion.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k summary_status -v`
Expected: FAIL — `summary_status` not in the PRAGMA column set.

- [ ] **Step 3: Write the migration**

Create `consensus/migrations/015_document_summary_status.sql`:

```sql
-- Summary generation outcome (issue #78 defect 1): a failed interpretation
-- call used to be persisted as the summary text itself. The summary column
-- now holds only real summaries; this column says why one is missing.
--   'ok'      summary generated, or the row predates this column
--   'failed'  generation was attempted and raised
--   'pending' generation was not attempted (no context/app, or opted out)
ALTER TABLE documents ADD COLUMN summary_status TEXT NOT NULL DEFAULT 'ok';
```

No registration step is needed — `migrator.py` auto-discovers `^(\d{3})_.*\.sql$`.

- [ ] **Step 4: Thread the column through the db layer**

In `consensus/db/documents.py`:

`add_document` gains a keyword parameter and writes it:

```python
    def add_document(
        self,
        filename: str,
        title: str,
        summary: str,
        mime_type: str,
        source_type: str,
        source_url: Optional[str],
        markdown: str,
        char_count: int,
        sections_json: str,
        summary_status: str = "ok",
    ) -> int:
        """Insert a new document and return its ID.

        Args:
            summary_status: Why ``summary`` holds what it holds — ``'ok'``,
                ``'failed'`` or ``'pending'`` (issue #78 defect 1).
        """
        cur = self._execute_write(
            "INSERT INTO documents "
            "(filename, title, summary, mime_type, source_type, source_url, "
            "markdown, char_count, sections_json, created_at, summary_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (filename, title, summary, mime_type, source_type, source_url,
             markdown, char_count, sections_json, time.time(),
             summary_status),
        )
        return cur.lastrowid
```

Add `summary_status` to the `SELECT` list and the returned dict of `get_document`, `get_all_documents` and `get_discussion_documents` (in the last, as `d.summary_status`). Each is a mechanical addition of one column at the end of the select and one key in the dict.

- [ ] **Step 5: Add the status constants**

Append to `consensus/tools_document/constants.py`:

```python
# Summary generation outcome recorded in documents.summary_status
SUMMARY_STATUS_OK = "ok"
SUMMARY_STATUS_FAILED = "failed"
SUMMARY_STATUS_PENDING = "pending"
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py -k summary_status -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/migrations/015_document_summary_status.sql \
        consensus/db/documents.py \
        consensus/tools_document/constants.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): add documents.summary_status

A failed summary is now recordable as such instead of being stored as
its own error text. Pre-existing rows default to 'ok' since they cannot
be retro-classified.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Ingestion records the status; `doc_list` renders it

**Files:**
- Modify: `consensus/tools_document/ingestion.py`, `consensus/tools_document/handlers.py:23-28` (`_summary_snippet`) and its three call sites (`:135`, `:149`, `:163`)
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Consumes: `SUMMARY_STATUS_*` constants (Task 3), `DocumentInterpretationError` (Task 2).
- Produces: `handlers._summary_snippet(summary: Optional[str], status: str) -> str`; `ingest_document`'s result dict gains `"summary_status"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document import handlers


def test_summary_snippet_reports_a_failed_status():
    """doc_list says the summary is missing rather than printing nothing."""
    from consensus.tools_document.constants import (
        SUMMARY_STATUS_FAILED, SUMMARY_STATUS_OK, SUMMARY_STATUS_PENDING,
    )
    assert "unavailable" in handlers._summary_snippet("", SUMMARY_STATUS_FAILED)
    assert "no summary" in handlers._summary_snippet(
        "", SUMMARY_STATUS_PENDING).lower()
    assert handlers._summary_snippet("A real one.", SUMMARY_STATUS_OK) == \
        "A real one."


@pytest.mark.asyncio
async def test_ingest_records_failed_status(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """A raising summary call is recorded as 'failed', not as 'ok'."""
    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("quota exceeded")

    patch_where_defined(
        monkeypatch, ingestion.ingest_document,
        "_call_interpretation_llm", boom,
    )

    class App:
        db = tmp_db

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await ingestion.ingest_document(
        app=App(), db=tmp_db, embed_client=None,
        content_bytes=b"# T\n\nBody.", filename="d.md",
        mime_type="text/markdown", context=context,
    )
    assert result["summary_status"] == "failed"
    assert tmp_db.get_document(result["document_id"])["summary_status"] == \
        "failed"


@pytest.mark.asyncio
async def test_ingest_without_context_records_pending(tmp_db):
    """Summary generation silently requires app+context; say so."""
    result = await ingestion.ingest_document(
        app=None, db=tmp_db, embed_client=None,
        content_bytes=b"# T\n\nBody.", filename="d.md",
        mime_type="text/markdown",
    )
    assert result["summary_status"] == "pending"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k "snippet or status" -v`
Expected: FAIL — `_summary_snippet() takes 1 positional argument but 2 were given`, and `KeyError: 'summary_status'`.

- [ ] **Step 3: Implement the ingestion side**

In `consensus/tools_document/ingestion.py`, replace the summary block from Task 2 Step 5 with one that tracks status, and pass it through:

```python
    # Generate summary.  A failed summary must never be persisted: the old
    # helper returned its error as a string, which was stored and then
    # reprinted to every participant by doc_list forever (issue #78).
    summary = ""
    summary_status = SUMMARY_STATUS_PENDING
    if generate_summary and context and app:
        try:
            excerpt = markdown[:SUMMARY_EXCERPT_CHARS]
            summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "You are a document analyst. Provide a brief summary "
                    "(2-3 sentences) of the following document excerpt. "
                    "Focus on the main topic, key findings or arguments."
                ),
                user_prompt=excerpt,
            )
            summary_status = SUMMARY_STATUS_OK
        except DocumentInterpretationError:
            logger.exception(
                "Summary generation failed for %s — storing no summary",
                filename,
            )
            summary = ""
            summary_status = SUMMARY_STATUS_FAILED
```

Pass `summary_status=summary_status` to `db.add_document(...)`, and add `"summary_status": summary_status` to the returned dict. Import the three constants from `.constants`.

- [ ] **Step 4: Implement the rendering side**

In `consensus/tools_document/handlers.py`:

```python
def _summary_snippet(summary: Optional[str], status: str) -> str:
    """Render a document summary for a one-line ``doc_list`` entry.

    A document with no usable summary says why (issue #78 defect 1):
    printing an empty line left an LLM failure looking like a document
    that simply had nothing to say.
    """
    text = (summary or "").strip()
    if text:
        if len(text) > SUMMARY_SNIPPET_CHARS:
            return text[:SUMMARY_SNIPPET_CHARS] + "..."
        return text
    if status == SUMMARY_STATUS_FAILED:
        return "(summary unavailable — generation failed)"
    return "(no summary)"
```

Update the three call sites to `_summary_snippet(doc["summary"], doc.get("summary_status", SUMMARY_STATUS_OK))`. Import `SUMMARY_STATUS_FAILED` and `SUMMARY_STATUS_OK` from `.constants`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py tests/test_tools_document_handlers.py -v`
Expected: the new tests PASS; update any existing `_summary_snippet` test for the new signature.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/tools_document/ingestion.py \
        consensus/tools_document/handlers.py \
        tests/test_tools_document_failures.py \
        tests/test_tools_document_handlers.py
git commit -m "$(cat <<'EOF'
fix(#78): record and render the summary generation outcome

ingest_document now stores 'failed' or 'pending' alongside an empty
summary and returns it, and doc_list says which it is rather than
printing a blank line that reads as a document with nothing to say.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Parsing stops manufacturing content

`_parse_pdf` returns the literal string `"(Empty PDF)"` — 11 non-blank characters, so `ingestion.py`'s `if not markdown.strip()` guard does not catch it, and a scanned PDF ingests "successfully" and is then answered from. `_parse_html` runs its regex tag-stripper with no log at all when trafilatura returns `None`, so a cookie banner plus inlined `<script>` bodies become the document. Any unrecognised MIME type is decoded into mojibake with a real char count.

**Files:**
- Modify: `consensus/tools_document/parsing.py:21-90`, `consensus/tools_document/constants.py`, `consensus/tools_document/ingestion.py:47-52`, `consensus/tools_document/__init__.py`
- Test: `tests/test_tools_document_failures.py`, `tests/test_tools_document.py`

**Interfaces:**
- Consumes: `DocumentParseError` (Task 2).
- Produces: `parsing.ParsedDocument(markdown: str, fidelity: str, notes: list[str])` — a frozen dataclass; `parse_document(content: bytes, filename: str, mime_type: str) -> ParsedDocument`. Constants `FIDELITY_FULL = "full"`, `FIDELITY_DEGRADED = "degraded"`, `MAX_REPLACEMENT_CHAR_RATIO = 0.1`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document import parsing
from consensus.tools_document.errors import DocumentParseError


def test_image_only_pdf_raises_instead_of_returning_placeholder():
    """A scanned PDF must not ingest as the string "(Empty PDF)".

    It was 11 non-blank characters, so the "empty after parsing" guard let
    it through; doc_ask then answered questions from it.
    """
    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        pages = [FakePage()]

        def __init__(self, *args):
            pass

    import sys
    import types
    fake = types.ModuleType("PyPDF2")
    fake.PdfReader = FakeReader
    sys.modules["PyPDF2"] = fake
    sys.modules.pop("pdfplumber", None)
    try:
        with pytest.raises(DocumentParseError) as exc:
            parsing.parse_document(b"%PDF-1.4 fake", "scan.pdf",
                                   "application/pdf")
    finally:
        del sys.modules["PyPDF2"]
    assert "scanned" in str(exc.value).lower()


def test_binary_content_raises_instead_of_mojibake():
    """A JPEG or .docx must not decode into replacement characters."""
    with pytest.raises(DocumentParseError) as exc:
        parsing.parse_document(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64,
            "photo.jpg", "image/jpeg",
        )
    assert "binary" in str(exc.value).lower()


def test_plain_text_parses_at_full_fidelity():
    """The normal path reports full fidelity and no notes."""
    parsed = parsing.parse_document(b"# Title\n\nBody.", "a.md",
                                    "text/markdown")
    assert parsed.markdown == "# Title\n\nBody."
    assert parsed.fidelity == "full"
    assert parsed.notes == []


def test_html_regex_fallback_is_marked_degraded_and_logged(caplog, monkeypatch):
    """When trafilatura yields nothing, say the extraction is low fidelity."""
    import sys
    import types
    fake = types.ModuleType("trafilatura")
    fake.extract = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "trafilatura", fake)

    with caplog.at_level(logging.WARNING):
        parsed = parsing.parse_document(
            b"<html><body><p>Hello</p></body></html>", "p.html", "text/html",
        )
    assert parsed.fidelity == "degraded"
    assert parsed.notes
    assert "fallback" in caplog.text.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k "pdf or binary or fidelity or fallback" -v`
Expected: FAIL — `parse_document` returns `str`, so `.markdown` raises `AttributeError`; the PDF case returns `"(Empty PDF)"` instead of raising.

- [ ] **Step 3: Add the constants**

Append to `consensus/tools_document/constants.py`:

```python
# Extraction fidelity reported by parse_document
FIDELITY_FULL = "full"
FIDELITY_DEGRADED = "degraded"

# Above this share of U+FFFD replacement characters, a "text" document is
# really binary that was decoded with errors="replace" (issue #78 defect 7).
MAX_REPLACEMENT_CHAR_RATIO = 0.1
```

- [ ] **Step 4: Rewrite the parsing functions**

In `consensus/tools_document/parsing.py`, add imports (`from dataclasses import dataclass, field`, `from .errors import DocumentParseError`, and the three new constants) and replace lines 21-90:

```python
@dataclass(frozen=True)
class ParsedDocument:
    """The outcome of turning document bytes into markdown.

    Attributes:
        markdown: The extracted text.
        fidelity: ``FIDELITY_FULL`` when a real extractor produced the
            text, ``FIDELITY_DEGRADED`` when a fallback did.  A degraded
            extraction is still usable, but the AI is told so rather than
            being handed cookie banners as the document (issue #78).
        notes: Human-readable reasons for a degraded result.
    """

    markdown: str
    fidelity: str = FIDELITY_FULL
    notes: list[str] = field(default_factory=list)


def parse_document(
    content: bytes, filename: str, mime_type: str,
) -> ParsedDocument:
    """Convert document bytes to markdown text.

    Supported formats:
    - PDF: pdfplumber (preferred) or PyPDF2 fallback
    - HTML: trafilatura, with a regex tag-stripper fallback
    - Plain text / Markdown: decoded as UTF-8

    Raises:
        DocumentParseError: If no usable text could be extracted, or the
            bytes are binary in a format this package cannot read.  It
            raises rather than synthesising placeholder content, which
            previously ingested as a real document (issue #78 defect 7).
    """
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _parse_pdf(content)
    if mime_type in ("text/html", "application/xhtml+xml") or \
            filename.lower().endswith((".html", ".htm")):
        return _parse_html(content)
    return _parse_text(content, filename, mime_type)


def _parse_text(
    content: bytes, filename: str, mime_type: str,
) -> ParsedDocument:
    """Decode plain text or markdown, rejecting binary payloads."""
    if b"\x00" in content:
        raise DocumentParseError(
            f"{filename} is binary, not text ({mime_type})",
            hint="only PDF, HTML, plain text and markdown can be ingested",
        )
    text = content.decode("utf-8", errors="replace")
    if text:
        ratio = text.count("�") / len(text)
        if ratio > MAX_REPLACEMENT_CHAR_RATIO:
            raise DocumentParseError(
                f"{filename} does not decode as UTF-8 text "
                f"({ratio:.0%} unreadable characters) — it looks binary",
                hint="only PDF, HTML, plain text and markdown can be ingested",
            )
    return ParsedDocument(markdown=text)


def _parse_pdf(content: bytes) -> ParsedDocument:
    """Extract text from PDF bytes, preferring pdfplumber."""
    try:
        import io

        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return ParsedDocument(markdown="\n\n".join(pages))
        logger.info("pdfplumber extracted no text, trying PyPDF2")
    except ImportError:
        logger.info("pdfplumber not available, trying PyPDF2")
    except Exception as e:
        logger.warning("pdfplumber failed: %s, trying PyPDF2", e)

    try:
        import io

        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {i + 1}\n\n{text}")
        if pages:
            return ParsedDocument(markdown="\n\n".join(pages))
    except ImportError:
        raise DocumentParseError(
            "PDF parsing requires pdfplumber or PyPDF2",
            hint="install with: uv pip install pdfplumber",
        )
    except Exception as e:
        raise DocumentParseError(
            f"PDF could not be read: {e}",
            hint="the file may be corrupt or password-protected",
        ) from e

    raise DocumentParseError(
        "No extractable text in this PDF — it looks scanned or image-only",
        hint="OCR the file before adding it",
    )


def _parse_html(content: bytes) -> ParsedDocument:
    """Extract readable text from HTML, marking regex fallbacks degraded."""
    html_text = content.decode("utf-8", errors="replace")
    try:
        import trafilatura
        text = trafilatura.extract(
            html_text, include_comments=False, include_tables=True,
        )
        if text:
            return ParsedDocument(markdown=text)
        logger.warning(
            "trafilatura extracted nothing — using the regex fallback; "
            "the page may be paywalled, consent-walled or JS-rendered",
        )
    except ImportError:
        logger.warning("trafilatura not available — using the regex fallback")
    except Exception as e:
        logger.warning("trafilatura failed (%s) — using the regex fallback", e)

    stripped = re.sub(r"<[^>]+>", "", html_text).strip()
    if not stripped:
        raise DocumentParseError(
            "No readable text could be extracted from this HTML",
            hint="the page may require JavaScript or be behind a paywall",
        )
    return ParsedDocument(
        markdown=stripped,
        fidelity=FIDELITY_DEGRADED,
        notes=[
            "Readability extraction failed; this text was produced by "
            "stripping HTML tags and may contain navigation, cookie "
            "banners or script content.",
        ],
    )
```

- [ ] **Step 5: Update the ingestion call site**

In `consensus/tools_document/ingestion.py`, replace lines 47-52:

```python
    # Parse to markdown.  Parsing raises rather than returning placeholder
    # text, so a scanned PDF no longer ingests as a real document.
    parsed = parse_document(content_bytes, filename, mime_type)
    markdown = parsed.markdown
    if not markdown.strip():
        return {"error": "Document is empty after parsing."}

    char_count = len(markdown)
```

Add `"fidelity": parsed.fidelity` and `"notes": parsed.notes` to the returned dict so a degraded extraction reaches the AI through `doc_add`'s result (the consumer requirement), and note it in the docstring.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py tests/test_tools_document.py -v`
Expected: new tests PASS. Existing `parse_document` tests asserting a `str` return need `.markdown` added — they were written against the old contract.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/tools_document/parsing.py \
        consensus/tools_document/constants.py \
        consensus/tools_document/ingestion.py \
        tests/test_tools_document_failures.py \
        tests/test_tools_document.py
git commit -m "$(cat <<'EOF'
fix(#78): never synthesize pseudo-content from a failed extraction

"(Empty PDF)" passed the empty-after-parsing guard, so a scanned PDF
ingested as a real document and doc_ask answered questions from the
placeholder. The HTML regex fallback ran unlogged, turning cookie
banners and inlined scripts into the document. Unrecognised binary
decoded into mojibake with a real char count.

parse_document now returns a ParsedDocument carrying an explicit
fidelity, raises DocumentParseError where no text exists, and logs every
fallback rung.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `fetch_url_content` retries and size cap

`parsing.py:93-111` does a single `client.get(url)` — a transient 502 or DNS blip fails the document add outright, an open golden-rule-5 violation. There is also no `content-length` cap, so a multi-GB URL is read fully into memory.

**Files:**
- Modify: `consensus/tools_document/parsing.py:93-111`, `consensus/tools_document/constants.py`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Produces: `fetch_url_content(url: str) -> tuple[bytes, str, str]` (unchanged signature). Constants `URL_FETCH_MAX_RETRIES = 3`, `URL_FETCH_BASE_DELAY = 1.0`, `MAX_DOCUMENT_BYTES = 50 * 1024 * 1024`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
import httpx


class _FakeAsyncClient:
    """Scripted httpx.AsyncClient replacement for fetch_url_content."""

    def __init__(self, outcomes, **kwargs):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok_response(body=b"hello", content_type="text/plain", headers=None):
    request = httpx.Request("GET", "https://example.com/a.txt")
    return httpx.Response(
        200, content=body, request=request,
        headers={"content-type": content_type, **(headers or {})},
    )


@pytest.mark.asyncio
async def test_fetch_retries_a_transient_failure(monkeypatch):
    """A timeout is retried with backoff rather than failing the add."""
    made = {}

    def factory(**kwargs):
        client = _FakeAsyncClient(
            [httpx.TimeoutException("slow"), _ok_response()],
        )
        made["client"] = client
        return client

    monkeypatch.setattr(parsing.httpx, "AsyncClient", factory)
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    content, filename, mime = await parsing.fetch_url_content(
        "https://example.com/a.txt")
    assert content == b"hello"
    assert made["client"].calls == 2


@pytest.mark.asyncio
async def test_fetch_does_not_retry_a_404(monkeypatch):
    """A client error is permanent — retrying it just wastes time."""
    request = httpx.Request("GET", "https://example.com/missing")
    not_found = httpx.Response(404, request=request)

    made = {}

    def factory(**kwargs):
        made["client"] = _FakeAsyncClient([not_found])
        return made["client"]

    monkeypatch.setattr(parsing.httpx, "AsyncClient", factory)
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    with pytest.raises(DocumentParseError):
        await parsing.fetch_url_content("https://example.com/missing")
    assert made["client"].calls == 1


@pytest.mark.asyncio
async def test_fetch_rejects_an_oversized_content_length(monkeypatch):
    """A declared multi-GB body is refused before it is read."""
    from consensus.tools_document.constants import MAX_DOCUMENT_BYTES

    oversized = _ok_response(
        headers={"content-length": str(MAX_DOCUMENT_BYTES + 1)})

    monkeypatch.setattr(
        parsing.httpx, "AsyncClient",
        lambda **kwargs: _FakeAsyncClient([oversized]),
    )
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    with pytest.raises(DocumentParseError) as exc:
        await parsing.fetch_url_content("https://example.com/big")
    assert "too large" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_fetch_rejects_an_oversized_undeclared_body(monkeypatch):
    """A header-less oversized body is caught after the read too."""
    from consensus.tools_document.constants import MAX_DOCUMENT_BYTES

    big = _ok_response(body=b"x" * (MAX_DOCUMENT_BYTES + 1))
    monkeypatch.setattr(
        parsing.httpx, "AsyncClient",
        lambda **kwargs: _FakeAsyncClient([big]),
    )
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    with pytest.raises(DocumentParseError):
        await parsing.fetch_url_content("https://example.com/big")
```

Add near the top of the test module:

```python
async def _no_sleep(_seconds):
    """Collapse backoff delays so retry tests stay fast."""
    return None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k fetch -v`
Expected: FAIL — one attempt only (`calls == 1` where 2 expected), and `httpx.HTTPStatusError` raised instead of `DocumentParseError`.

- [ ] **Step 3: Add the constants**

Append to `consensus/tools_document/constants.py`:

```python
# URL fetching retry policy (golden rule 5)
URL_FETCH_MAX_RETRIES = 3
URL_FETCH_BASE_DELAY = 1.0  # seconds, doubled per attempt

# Largest document accepted from a URL, in bytes. Checked against the
# content-length header and again after reading, since a header-less
# response would otherwise be read fully into memory.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
```

- [ ] **Step 4: Rewrite `fetch_url_content`**

Add `import asyncio` to `parsing.py` and replace lines 93-111:

```python
def _filename_for(url: str, mime_type: str) -> str:
    """Derive a filename with a useful extension from a URL and MIME type."""
    from urllib.parse import urlparse
    path = urlparse(url).path
    filename = path.split("/")[-1] or "document"
    if not filename.endswith((".pdf", ".html", ".htm", ".txt", ".md")):
        if "pdf" in mime_type:
            filename += ".pdf"
        elif "html" in mime_type:
            filename += ".html"
    return filename


async def fetch_url_content(url: str) -> tuple[bytes, str, str]:
    """Fetch a document from a URL.

    Retries transient failures — timeouts, connection errors and 5xx — up
    to ``URL_FETCH_MAX_RETRIES`` times with exponential backoff (golden
    rule 5).  A 4xx is permanent and raises immediately.

    Returns:
        ``(content_bytes, filename, mime_type)``.

    Raises:
        DocumentParseError: If the fetch fails, or the body exceeds
            ``MAX_DOCUMENT_BYTES``.
    """
    last_exc: Exception | None = None
    for attempt in range(URL_FETCH_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(
                timeout=URL_FETCH_TIMEOUT, follow_redirects=True,
            ) as client:
                response = await client.get(url)

                if 400 <= response.status_code < 500:
                    raise DocumentParseError(
                        f"{url} returned HTTP {response.status_code}",
                        hint="check the address, or whether it needs a login",
                    )
                response.raise_for_status()

                declared = response.headers.get("content-length")
                if declared and int(declared) > MAX_DOCUMENT_BYTES:
                    raise DocumentParseError(
                        f"{url} is too large ({int(declared)} bytes; the "
                        f"limit is {MAX_DOCUMENT_BYTES})",
                        hint="download it and add the relevant extract",
                    )

                content = response.content
                if len(content) > MAX_DOCUMENT_BYTES:
                    raise DocumentParseError(
                        f"{url} is too large ({len(content)} bytes; the "
                        f"limit is {MAX_DOCUMENT_BYTES})",
                        hint="download it and add the relevant extract",
                    )

                content_type = response.headers.get(
                    "content-type", "text/html")
                mime_type = content_type.split(";")[0].strip()
                return content, _filename_for(url, mime_type), mime_type

        except DocumentParseError:
            raise
        except (httpx.TimeoutException, httpx.HTTPStatusError,
                httpx.TransportError) as e:
            last_exc = e
            if attempt == URL_FETCH_MAX_RETRIES - 1:
                break
            delay = URL_FETCH_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Fetch of %s failed (attempt %d/%d: %s), retrying in %.1fs",
                url, attempt + 1, URL_FETCH_MAX_RETRIES, e, delay,
            )
            await asyncio.sleep(delay)

    raise DocumentParseError(
        f"Could not fetch {url} after {URL_FETCH_MAX_RETRIES} attempts: "
        f"{last_exc}",
        hint="check the address and that the host is reachable",
    )
```

Import the three new constants.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py -k fetch -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`

Note: `app.add_document_from_url` catches `Exception` around `fetch_url_content` and returns `{"error": f"Failed to fetch URL: {e}"}` — a `DocumentParseError`'s `str()` includes its hint, so that path improves with no change.

```bash
git add consensus/tools_document/parsing.py \
        consensus/tools_document/constants.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): retry URL fetches and cap document size

fetch_url_content made a single unretried request, so a transient 502 or
DNS blip failed the document add outright (golden rule 5). It now retries
timeouts, transport errors and 5xx with exponential backoff, raises
immediately on 4xx, and refuses bodies over MAX_DOCUMENT_BYTES on both
the declared content-length and the actual read.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The embedding pass cannot die silently

`_embed_document_chunks` has a `finally` but no `except`: a `sqlite3.OperationalError: database is locked` from `get_document_chunks`, or anything raised inside its `except EmbeddingContextLengthError` block (a sibling `except` cannot catch it), kills the pass with nothing logged.

**Files:**
- Modify: `consensus/tools_document/embedding.py:158-181`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Produces: `embedding.IndexingFailure(consecutive_failures: int, last_error: str, last_attempt: float)` dataclass; `embedding._indexing_failures: dict[int, IndexingFailure]`; `embedding.get_indexing_failure(doc_id: int) -> Optional[IndexingFailure]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document import embedding


@pytest.fixture(autouse=True)
def _clear_indexing_state():
    """Module-level indexing state must not leak between tests."""
    yield
    embedding._indexing_failures.clear()
    embedding._embedding_docs.clear()


@pytest.mark.asyncio
async def test_pass_crash_is_logged_not_swallowed(tmp_db, caplog):
    """A crash inside the pass logs a traceback and records a failure."""
    class ExplodingDb:
        def get_document_chunks(self, doc_id):
            raise RuntimeError("database is locked")

    with caplog.at_level(logging.ERROR):
        await embedding._embed_document_chunks(7, ExplodingDb(), object())

    assert "database is locked" in caplog.text
    failure = embedding.get_indexing_failure(7)
    assert failure is not None
    assert failure.consecutive_failures == 1
    assert "database is locked" in failure.last_error
    assert 7 not in embedding._embedding_docs


@pytest.mark.asyncio
async def test_failed_chunks_record_a_failure(tmp_db):
    """A pass that completes with failed chunks is still a failure."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="c.md", title="C", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# C\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)

    client = FakeEmbedClient(error=RuntimeError("model not found"))
    await embedding._embed_document_chunks(doc_id, tmp_db, client)

    failure = embedding.get_indexing_failure(doc_id)
    assert failure is not None
    assert failure.consecutive_failures == 1


@pytest.mark.asyncio
async def test_consecutive_failures_accumulate_and_clear(tmp_db):
    """Repeat failures count up; a clean pass wipes the record."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="d.md", title="D", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# D\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)

    failing = FakeEmbedClient(error=RuntimeError("down"))
    await embedding._embed_document_chunks(doc_id, tmp_db, failing)
    await embedding._embed_document_chunks(doc_id, tmp_db, failing)
    assert embedding.get_indexing_failure(doc_id).consecutive_failures == 2

    await embedding._embed_document_chunks(
        doc_id, tmp_db, FakeEmbedClient(vector=[1.0, 0.0, 0.0]))
    assert embedding.get_indexing_failure(doc_id) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k "pass_crash or failed_chunks or consecutive" -v`
Expected: FAIL — `AttributeError: module has no attribute '_indexing_failures'`, and the crash test propagates `RuntimeError` out of the function.

- [ ] **Step 3: Implement the failure state**

In `consensus/tools_document/embedding.py`, add `import time`, `from dataclasses import dataclass`, `from typing import Optional`, and after the `_embedding_docs` declaration:

```python
@dataclass
class IndexingFailure:
    """A document's most recent unsuccessful embedding pass.

    Recorded so that ``doc_ask`` can tell a genuinely in-flight first pass
    from an embedder that is down: the latter used to be reported forever
    as "still being indexed, please try again shortly" (issue #78).
    """

    consecutive_failures: int
    last_error: str
    last_attempt: float


# Documents whose last embedding pass did not fully succeed.
_indexing_failures: dict[int, IndexingFailure] = {}


def get_indexing_failure(doc_id: int) -> Optional[IndexingFailure]:
    """Return the recorded failure for *doc_id*, or None if the last pass
    succeeded (or none has run)."""
    return _indexing_failures.get(doc_id)


def _record_indexing_failure(doc_id: int, error: str) -> None:
    """Record or increment a document's consecutive failure count."""
    previous = _indexing_failures.get(doc_id)
    _indexing_failures[doc_id] = IndexingFailure(
        consecutive_failures=(
            previous.consecutive_failures + 1 if previous else 1),
        last_error=error,
        last_attempt=time.time(),
    )


def _clear_indexing_failure(doc_id: int) -> None:
    """Forget a document's failure record after a fully clean pass."""
    _indexing_failures.pop(doc_id, None)
```

- [ ] **Step 4: Guard the pass**

Replace `_embed_document_chunks` (lines 158-181):

```python
async def _embed_document_chunks(doc_id: int, db, embed_client) -> None:
    """Background task: embed all unembedded chunks for a document.

    Never raises.  It runs detached, so an escaping exception would be
    visible only as asyncio's GC-time warning; and its outcome is recorded
    in ``_indexing_failures`` so ``doc_ask`` can distinguish a first pass
    still in flight from an embedder that is down (issue #78 defect 2, 3).
    """
    try:
        chunks = db.get_document_chunks(doc_id)
        existing = db.get_chunks_with_embeddings(doc_id)
        embedded_ids = {c["id"] for c in existing}

        failed_chunks = []
        for chunk in chunks:
            if chunk["id"] in embedded_ids:
                continue
            ok = await _embed_single_chunk(chunk, doc_id, db, embed_client)
            if ok:
                embedded_ids.add(chunk["id"])
            else:
                failed_chunks.append(chunk)

        if failed_chunks:
            detail = (
                f"{len(failed_chunks)}/{len(chunks)} chunks could not be "
                "embedded"
            )
            logger.warning("Doc %d: %s", doc_id, detail)
            _record_indexing_failure(doc_id, detail)
        else:
            _clear_indexing_failure(doc_id)

    except Exception as e:
        # A sibling `except` inside _embed_single_chunk cannot catch what
        # its own handler raises, and a locked database raises right here.
        logger.exception("Embedding pass for doc %d failed", doc_id)
        _record_indexing_failure(doc_id, str(e))
    finally:
        _embedding_docs.discard(doc_id)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tools_document_failures.py -k "pass_crash or failed_chunks or consecutive" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/tools_document/embedding.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): guard the background embedding pass and record its outcome

_embed_document_chunks had a finally but no except, so a locked database
or an exception raised inside its EmbeddingContextLengthError handler
killed the pass with nothing logged. It now logs the traceback and
records a per-document consecutive failure count, cleared by a clean
pass, which the next task uses to stop reporting a dead embedder as a
transient delay.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Relevance floor and dimension-mismatch reporting

`handlers.py:345` calls `_rank_by_similarity(query_vec, rows, RAG_TOP_K)` with the default `threshold=0.0`, while `doc_list` at `:93` correctly passes `MIN_SIMILARITY_THRESHOLD`. Combined with silent dimension mismatches, an embedding-model switch scores every row 0.0, the stable sort returns the first five rows in DB order, and the LLM is handed five arbitrary passages labelled "relevance: 0.0" with instructions to answer only from them.

**Files:**
- Modify: `consensus/tools_document/embedding.py:40-52`, `consensus/tools_document/handlers.py:106-109` and `:356`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Produces: `embedding.RankingResult(ranked: list[tuple[float, dict]], skipped_dim_mismatch: int, query_dim: int, row_dims: tuple[int, ...])`; `_rank_by_similarity(...) -> RankingResult`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document.embedding import _pack_embedding


def test_ranking_reports_dimension_mismatches():
    """Rows embedded by a different model are counted, not silently zeroed."""
    rows = [
        {"id": 1, "embedding": _pack_embedding([1.0, 0.0, 0.0])},
        {"id": 2, "embedding": _pack_embedding([1.0, 0.0])},
    ]
    result = embedding._rank_by_similarity([1.0, 0.0, 0.0], rows, limit=5)

    assert result.skipped_dim_mismatch == 1
    assert result.query_dim == 3
    assert 2 in result.row_dims
    assert [row["id"] for _score, row in result.ranked] == [1]


def test_ranking_applies_the_threshold():
    """Rows below the floor are excluded from ranked."""
    rows = [
        {"id": 1, "embedding": _pack_embedding([1.0, 0.0, 0.0])},
        {"id": 2, "embedding": _pack_embedding([0.0, 1.0, 0.0])},
    ]
    result = embedding._rank_by_similarity(
        [1.0, 0.0, 0.0], rows, limit=5, threshold=0.3)
    assert [row["id"] for _score, row in result.ranked] == [1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k ranking -v`
Expected: FAIL — `AttributeError: 'list' object has no attribute 'skipped_dim_mismatch'`

- [ ] **Step 3: Implement `RankingResult`**

In `consensus/tools_document/embedding.py`, replace `_rank_by_similarity` (lines 40-52):

```python
@dataclass
class RankingResult:
    """Ranked rows plus what was discarded reaching them.

    ``skipped_dim_mismatch`` is what makes an embedding-model change
    diagnosable: differing dimensions score 0.0, so without the count a
    re-index requirement is indistinguishable from a document that simply
    does not address the question (issue #78 defects 4, 5).
    """

    ranked: list[tuple[float, dict]]
    skipped_dim_mismatch: int = 0
    query_dim: int = 0
    row_dims: tuple[int, ...] = ()


def _rank_by_similarity(
    query_vec: list[float], rows: list[dict], limit: int,
    threshold: float = 0.0,
) -> RankingResult:
    """Sort rows by cosine similarity, keeping the top *limit* above
    *threshold*.

    Dimension mismatches are counted here rather than logged inside
    ``_cosine_similarity``, which stays a pure function called once per
    row (golden rule 1).
    """
    scored: list[tuple[float, dict]] = []
    mismatched = 0
    mismatched_dims: set[int] = set()
    for row in rows:
        emb = _unpack_embedding(row["embedding"])
        if len(emb) != len(query_vec):
            mismatched += 1
            mismatched_dims.add(len(emb))
            continue
        score = _cosine_similarity(query_vec, emb)
        if score >= threshold:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)

    if mismatched:
        logger.warning(
            "%d chunk(s) skipped: embedded at dimension(s) %s but the query "
            "is %d — the embedding model changed",
            mismatched, sorted(mismatched_dims), len(query_vec),
        )

    return RankingResult(
        ranked=scored[:limit],
        skipped_dim_mismatch=mismatched,
        query_dim=len(query_vec),
        row_dims=tuple(sorted(mismatched_dims)),
    )
```

- [ ] **Step 4: Update both call sites**

In `consensus/tools_document/handlers.py`, `_doc_list_handler` (line 106):

```python
        ranking = _rank_by_similarity(
            query_vec, rows, limit=LIBRARY_SEARCH_LIMIT,
            threshold=MIN_SIMILARITY_THRESHOLD,
        )
        scored = ranking.ranked
```

and after the `if not seen_docs:` check, report the mismatch rather than claiming an empty library:

```python
        if not seen_docs:
            if ranking.skipped_dim_mismatch:
                return ToolResult(
                    content=_reindex_message(ranking), is_error=True)
            return ToolResult(content=f"No documents match '{query}'.")
```

Add the shared message helper to `handlers.py`:

```python
def _reindex_message(ranking) -> str:
    """Explain a dimension mismatch in terms a user can act on."""
    return (
        f"{ranking.skipped_dim_mismatch} chunk(s) were indexed with a "
        f"different embedding model (dimension "
        f"{', '.join(str(d) for d in ranking.row_dims)} vs "
        f"{ranking.query_dim} now). The documents must be re-indexed "
        "before they can be searched."
    )
```

In `_doc_ask_handler` (line 356), pass the floor and branch on the two empty outcomes:

```python
    ranking = _rank_by_similarity(
        query_vec, rows, RAG_TOP_K, threshold=MIN_SIMILARITY_THRESHOLD,
    )
    if not ranking.ranked:
        # Two very different situations used to look identical, because
        # the default threshold of 0.0 let every row through (issue #78).
        if ranking.skipped_dim_mismatch:
            return ToolResult(content=_reindex_message(ranking), is_error=True)
        return ToolResult(
            content=(
                f"No passage in '{doc['title']}' is relevant to that "
                "question (nothing scored above the relevance threshold)."
            ),
        )
    scored = ranking.ranked
```

- [ ] **Step 5: Write the handler-level test**

Append to `tests/test_tools_document_failures.py`:

```python
@pytest.mark.asyncio
async def test_doc_ask_reports_a_dimension_mismatch(tmp_db, sample_ai_entity):
    """A model switch is reported as needing a re-index, not answered."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="e.md", title="E", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# E\n\nBody.",
        char_count=9, sections_json="[]",
    )
    chunk_id = tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    tmp_db.set_chunk_embedding(chunk_id, _pack_embedding([1.0, 0.0]))

    client = FakeEmbedClient(vector=[1.0, 0.0, 0.0])
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers._doc_ask_handler(
        {"document_id": doc_id, "question": "what?"},
        context, tmp_db, client, None,
    )

    assert result.is_error
    assert "re-indexed" in result.content
```

- [ ] **Step 6: Run the tests, then the full suite, then commit**

Run: `uv run pytest tests/test_tools_document_failures.py tests/test_tools_document_handlers.py -v`
Then: `uv run pytest -q`

Existing tests calling `_rank_by_similarity` and expecting a list must be updated to read `.ranked` — they were written against the old contract.

```bash
git add consensus/tools_document/embedding.py \
        consensus/tools_document/handlers.py \
        tests/test_tools_document_failures.py \
        tests/test_tools_document.py \
        tests/test_tools_document_handlers.py
git commit -m "$(cat <<'EOF'
fix(#78): give RAG retrieval a relevance floor and report model changes

doc_ask ranked with the default threshold of 0.0, so an embedding
dimension mismatch scored every row 0.0 and the LLM was handed the first
five rows in DB order, labelled "relevance: 0.0", with instructions to
answer only from them. It now applies MIN_SIMILARITY_THRESHOLD like
doc_list does, and distinguishes "nothing relevant" from "these chunks
were indexed by a different model; re-index required".

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: A dead embedder stops reporting itself as a delay

`handlers.py:313-330` returns *"Document is still being indexed (0/N chunks embedded). Please try again shortly."* as a **non-error** whenever chunks are unembedded. If the embedding service is down, every call re-spawns a pass that fails and returns the same encouraging message; the AI retries up to `MAX_TOOL_ITERATIONS` each turn, and the user sees a discussion that silently never uses the document.

**Files:**
- Modify: `consensus/tools_document/handlers.py:324-341`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Consumes: `embedding.get_indexing_failure` (Task 7).
- Produces: `handlers._post_indexing_notice(app, doc_id: int, detail: str) -> None`; `handlers._notified_index_failures: set[int]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.models import Discussion, Entity, EntityType, MessageRole


class _NoticeApp:
    """App stand-in carrying a real Discussion so post_notice can run."""

    def __init__(self, db, moderator: Entity, discussion: Discussion):
        self.db = db
        self.discussion = discussion
        self._moderator = moderator

    def _resolve_key_for_moderator(self, provider_id, env_name):
        return "k"


@pytest.fixture
def notice_app(tmp_db, sample_ai_entity):
    """An app whose discussion can receive a transcript notice."""
    mod_id = tmp_db.add_entity(
        "Mod", "human", "#00ff00", None, "", 0.5, 512, "")
    disc_id = tmp_db.create_discussion("T", "topic", mod_id)
    discussion = Discussion(
        id=disc_id, title="T", topic="topic", moderator_id=mod_id,
    )
    moderator = Entity(
        id=mod_id, name="Mod", entity_type=EntityType.HUMAN, color="#00ff00",
    )
    return _NoticeApp(tmp_db, moderator, discussion)


@pytest.mark.asyncio
async def test_first_pass_still_reports_as_indexing(tmp_db, notice_app):
    """With no recorded failure, "still indexing" is the honest answer."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="f.md", title="F", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# F\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)

    context = ToolContext(caller_entity_id=0, discussion_id=notice_app.discussion.id)
    result = await handlers._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )
    assert not result.is_error
    assert "still being indexed" in result.content


@pytest.mark.asyncio
async def test_failed_indexing_is_an_error_with_the_real_cause(
    tmp_db, notice_app,
):
    """After a failed pass, doc_ask errors and names the embedder problem."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="g.md", title="G", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# G\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embedding._record_indexing_failure(
        doc_id, "Cannot connect to embedding service at localhost:11434")

    context = ToolContext(caller_entity_id=0, discussion_id=notice_app.discussion.id)
    result = await handlers._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    assert result.is_error
    assert "localhost:11434" in result.content
    assert "try again shortly" not in result.content


@pytest.mark.asyncio
async def test_indexing_failure_posts_one_transcript_notice(
    tmp_db, notice_app,
):
    """The human sees it in the transcript, once per failure streak."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="h.md", title="H", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# H\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embedding._record_indexing_failure(doc_id, "embedder down")

    context = ToolContext(caller_entity_id=0, discussion_id=notice_app.discussion.id)
    for _ in range(3):
        await handlers._doc_ask_handler(
            {"document_id": doc_id, "question": "q"},
            context, tmp_db, FakeEmbedClient(), notice_app,
        )

    notices = [
        m for m in notice_app.discussion.messages
        if m.role == MessageRole.SYSTEM and "embedder down" in m.content
    ]
    assert len(notices) == 1


@pytest.mark.asyncio
async def test_notice_failure_does_not_break_the_tool_call(tmp_db):
    """A missing discussion must not turn a report into a crash."""
    from tests.document_helpers import FakeEmbedClient

    class NoDiscussionApp:
        def __init__(self, db):
            self.db = db
            self.discussion = None

    doc_id = tmp_db.add_document(
        filename="i.md", title="I", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# I\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embedding._record_indexing_failure(doc_id, "embedder down")

    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), NoDiscussionApp(tmp_db),
    )
    assert result.is_error
    assert "embedder down" in result.content
```

Extend the autouse fixture from Task 7 to also clear `handlers._notified_index_failures`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k "indexing or first_pass" -v`
Expected: FAIL — every call returns the non-error "still being indexed" message; no notice is posted.

- [ ] **Step 3: Implement the notice helper**

In `consensus/tools_document/handlers.py`:

```python
# Documents whose indexing failure has already been announced in the
# transcript. The AI retries doc_ask up to MAX_TOOL_ITERATIONS per turn,
# and one notice per streak is information while five is noise.
_notified_index_failures: set[int] = set()


def _post_indexing_notice(app, doc_id: int, detail: str) -> None:
    """Announce an indexing failure in the discussion transcript.

    Golden rule 6: the error must reach the UI, and the tool-call row is
    collapsed by default, so a human who is not expanding tool calls would
    otherwise never learn the document is unusable.

    Fully guarded — this reports a failure, so it must not be able to
    raise one of its own out of the handler (the lesson of issue #74).
    """
    if doc_id in _notified_index_failures:
        return
    try:
        from ..app_discussion_flow.helpers import post_notice

        discussion = getattr(app, "discussion", None)
        if discussion is None:
            return
        moderator = app.db.get_entity(discussion.moderator_id)
        if not moderator:
            return
        from ..models import Entity
        post_notice(
            discussion, app.db, Entity.from_db_row(moderator),
            f"Document {doc_id} could not be indexed: {detail}. "
            "Participants cannot search or ask questions about it until "
            "the embedding service is working and the document is "
            "re-indexed.",
        )
        _notified_index_failures.add(doc_id)
    except Exception:
        logger.exception(
            "Could not post the indexing-failure notice for doc %d", doc_id)
```

> Check `Entity.from_db_row`'s real name in `consensus/models.py` before writing this — if the codebase constructs entities differently, follow that pattern. The requirement is a real `Entity` for `post_notice`'s third argument.

- [ ] **Step 4: Branch the unembedded path**

Replace lines 324-341 of `_doc_ask_handler`:

```python
    # Check whether embeddings are ready.  A failed pass and a first pass
    # still in flight used to be reported identically, so a dead embedder
    # claimed to be "still indexing" forever (issue #78 defect 3).
    unembedded = db.count_unembedded_chunks(doc_id)
    if unembedded > 0:
        total_chunks = len(db.get_document_chunks(doc_id))
        embedded = total_chunks - unembedded
        failure = get_indexing_failure(doc_id)

        if failure is not None:
            detail = failure.last_error
            _post_indexing_notice(app, doc_id, detail)
            return ToolResult(
                content=(
                    f"Indexing failed: {detail}. "
                    f"{embedded}/{total_chunks} chunks embedded after "
                    f"{failure.consecutive_failures} failed pass(es). "
                    "The embedding service must be working before this "
                    "document can be queried."
                ),
                is_error=True,
            )

        if embed_client and doc_id not in _embedding_docs:
            _embedding_docs.add(doc_id)
            _spawn_embedding_pass(doc_id, db, embed_client)
        return ToolResult(
            content=(
                f"Document is still being indexed "
                f"({embedded}/{total_chunks} chunks embedded). "
                "Please try again shortly."
            ),
        )
```

Import `get_indexing_failure` from `.embedding`.

- [ ] **Step 5: Run the tests, then the full suite, then commit**

Run: `uv run pytest tests/test_tools_document_failures.py -k "indexing or first_pass or notice" -v`
Then: `uv run pytest -q`

```bash
git add consensus/tools_document/handlers.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): stop reporting a dead embedder as a transient delay

When chunks were unembedded, doc_ask returned "still being indexed,
please try again shortly" as a non-error regardless of why. With the
embedding service down, every call re-spawned a pass that failed and
returned the same encouraging message: the AI burned its tool-iteration
budget each turn and the human never learned the document was unusable.

It now branches on the recorded failure state, errors with the
embedder's real message, and posts one transcript notice per failure
streak so the failure survives a collapsed tool-call row.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Range validation

`handlers.py:195-202` takes `from_char`/`to_char` straight from the model unvalidated: `markdown[500:100]` returns `""` in a **non-error** result with `"length": 0`, and `to_char=-5` silently drops the last five characters via negative slicing. `_doc_summary_handler:411` already guards, so the inconsistency looks accidental.

**Files:**
- Create: `consensus/tools_document/validation.py`
- Modify: `consensus/tools_document/handlers.py:190-213` and `:405-426`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Produces: `validation.resolve_range(from_char: int, to_char: int, length: int) -> tuple[int, int]`, raising `ValueError`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document.validation import resolve_range


def test_resolve_range_expands_the_sentinel():
    """-1 means "to the end", as the tool schema documents."""
    assert resolve_range(0, -1, 100) == (0, 100)


def test_resolve_range_clamps_beyond_the_end():
    """A to_char past the end is a harmless overshoot, not an error."""
    assert resolve_range(10, 500, 100) == (10, 100)


def test_resolve_range_rejects_an_inverted_range():
    """markdown[500:100] silently returned "" with length 0."""
    with pytest.raises(ValueError, match="before"):
        resolve_range(500, 100, 1000)


def test_resolve_range_rejects_other_negatives():
    """-5 used to drop the last five characters via negative slicing."""
    with pytest.raises(ValueError):
        resolve_range(0, -5, 100)
    with pytest.raises(ValueError):
        resolve_range(-3, 50, 100)


def test_resolve_range_rejects_a_start_past_the_end():
    """A from_char beyond the document is a mistake worth reporting."""
    with pytest.raises(ValueError):
        resolve_range(200, -1, 100)


@pytest.mark.asyncio
async def test_doc_get_text_rejects_an_inverted_range(tmp_db):
    """The handler reports it instead of returning an empty success."""
    doc_id = tmp_db.add_document(
        filename="j.md", title="J", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None,
        markdown="# J\n\n" + "x" * 1000, char_count=1005, sections_json="[]",
    )
    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers._doc_get_text_handler(
        {"document_id": doc_id, "from_char": 500, "to_char": 100},
        context, tmp_db, None, None,
    )
    assert result.is_error
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k "resolve_range or get_text" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.tools_document.validation'`

- [ ] **Step 3: Write the module**

Create `consensus/tools_document/validation.py`:

```python
"""Pure validation helpers for document tool arguments.

Character ranges arrive straight from the model, where an inverted or
negative range is a plausible mistake.  Validating in one place keeps
``doc_get_text`` and ``doc_summary`` consistent — only the latter guarded,
and the inconsistency looked accidental (issue #78 defect 9).
"""

# Documented sentinel meaning "to the end of the document".
TO_END = -1


def resolve_range(from_char: int, to_char: int, length: int) -> tuple[int, int]:
    """Resolve a model-supplied character range against a document length.

    Args:
        from_char: Inclusive start offset.
        to_char: Exclusive end offset, or ``TO_END`` for the end of the
            document.
        length: The document's character count.

    Returns:
        The resolved ``(from_char, to_char)``, with ``to_char`` clamped to
        *length*.

    Raises:
        ValueError: If either offset is negative other than the ``TO_END``
            sentinel, if the start is past the end of the document, or if
            the start is not before the end.  Python's negative slicing
            would otherwise turn ``to_char=-5`` into a silent truncation
            and ``markdown[500:100]`` into an empty success.
    """
    if from_char < 0:
        raise ValueError(
            f"from_char must not be negative (got {from_char})")
    if to_char < 0 and to_char != TO_END:
        raise ValueError(
            f"to_char must be {TO_END} (end of document) or a non-negative "
            f"offset (got {to_char})")
    if from_char > length:
        raise ValueError(
            f"from_char {from_char} is past the end of the document "
            f"({length} characters)")

    resolved_to = length if to_char == TO_END else min(to_char, length)
    if from_char >= resolved_to:
        raise ValueError(
            f"from_char {from_char} must be before to_char {resolved_to}")
    return from_char, resolved_to
```

- [ ] **Step 4: Use it in both handlers**

In `_doc_get_text_handler`, replace the `if to_char == -1:` block:

```python
    try:
        from_char, to_char = resolve_range(from_char, to_char, len(markdown))
    except ValueError as e:
        return ToolResult(content=f"Invalid range: {e}", is_error=True)
    text = markdown[from_char:to_char]
```

Apply the identical replacement in `_doc_summary_handler`, dropping its now-redundant `if not text.strip()` early return only if `resolve_range` already covers it — it does not (a range of whitespace is valid), so keep that check. Import `resolve_range` from `.validation`.

- [ ] **Step 5: Run the tests, then the full suite, then commit**

Run: `uv run pytest tests/test_tools_document_failures.py -k "resolve_range or get_text" -v`
Then: `uv run pytest -q`

```bash
git add consensus/tools_document/validation.py \
        consensus/tools_document/handlers.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): validate model-supplied character ranges

doc_get_text passed from_char/to_char straight to a slice, so
markdown[500:100] returned "" in a non-error result with length 0, and
to_char=-5 silently dropped the last five characters through negative
slicing. Both doc_get_text and doc_summary now share one resolve_range
helper, closing the inconsistency where only the latter guarded.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Chapters carry their subsections

`extract_sections` sets `to_char` to the next header *at any level*, so `doc_get_chapter` on a section with subsections returns only its preamble — while the tool description the model reads promises "the full text of a named section".

**Files:**
- Modify: `consensus/tools_document/validation.py`, `consensus/tools_document/handlers.py:246-303`, `consensus/tools_document/provider.py:100-110`
- Test: `tests/test_tools_document_failures.py`

**Interfaces:**
- Produces: `validation.chapter_range(sections: list[dict], index: int, length: int) -> tuple[int, int, list[str]]` returning `(from_char, to_char, subsection_headers)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_document_failures.py`:

```python
from consensus.tools_document.validation import chapter_range

_SECTIONS = [
    {"header": "Intro", "level": 1, "from_char": 0, "to_char": 10},
    {"header": "Methods", "level": 2, "from_char": 10, "to_char": 20},
    {"header": "Participants", "level": 3, "from_char": 20, "to_char": 30},
    {"header": "Procedure", "level": 3, "from_char": 30, "to_char": 40},
    {"header": "Results", "level": 2, "from_char": 40, "to_char": 50},
]


def test_chapter_range_includes_subsections():
    """A chapter runs to the next header at the same or a higher level."""
    start, end, subs = chapter_range(_SECTIONS, 1, 50)
    assert (start, end) == (10, 40)
    assert subs == ["Participants", "Procedure"]


def test_chapter_range_of_a_leaf_section_is_unchanged():
    """A section with no subsections keeps its original extent."""
    start, end, subs = chapter_range(_SECTIONS, 2, 50)
    assert (start, end) == (20, 30)
    assert subs == []


def test_chapter_range_of_the_last_section_runs_to_the_end():
    """Nothing follows, so the chapter ends with the document."""
    start, end, subs = chapter_range(_SECTIONS, 4, 50)
    assert (start, end) == (40, 50)


def test_chapter_range_of_a_top_level_section_spans_everything_under_it():
    """Level 1 swallows every deeper header that follows."""
    start, end, subs = chapter_range(_SECTIONS, 0, 50)
    assert (start, end) == (0, 50)
    assert "Methods" in subs


@pytest.mark.asyncio
async def test_doc_get_chapter_returns_subsection_text(tmp_db):
    """The handler returns the whole chapter, not just its preamble."""
    import json
    markdown = (
        "# Intro\n\nIntro body.\n\n"
        "## Methods\n\nMethods preamble.\n\n"
        "### Participants\n\nTwelve adults.\n\n"
        "## Results\n\nResults body.\n"
    )
    from consensus.tools_document.parsing import extract_sections
    doc_id = tmp_db.add_document(
        filename="k.md", title="K", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown=markdown,
        char_count=len(markdown),
        sections_json=json.dumps(extract_sections(markdown)),
    )
    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers._doc_get_chapter_handler(
        {"document_id": doc_id, "header": "Methods"},
        context, tmp_db, None, None,
    )
    assert "Methods preamble." in result.content
    assert "Twelve adults." in result.content
    assert "Results body." not in result.content
    assert result.metadata["subsections_included"] == ["Participants"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools_document_failures.py -k chapter -v`
Expected: FAIL — `ImportError: cannot import name 'chapter_range'`; the handler test returns only the preamble.

- [ ] **Step 3: Implement `chapter_range`**

Append to `consensus/tools_document/validation.py`:

```python
def chapter_range(
    sections: list[dict], index: int, length: int,
) -> tuple[int, int, list[str]]:
    """Return the full extent of the chapter at *index*, with its
    subsections.

    ``extract_sections`` ends every section at the next header of *any*
    level, because that is what chunk boundaries need.  A chapter is a
    different question: ``## Methods`` includes its ``### Participants``,
    so the range runs to the next header at the same or a higher level
    (issue #78 defect 10).  The stored ``sections_json`` is untouched, so
    no document needs re-ingesting.

    Args:
        sections: The document's sections, in document order.
        index: Which section to treat as the chapter head.
        length: The document's character count, used when the chapter runs
            to the end.

    Returns:
        ``(from_char, to_char, subsection_headers)``.
    """
    head = sections[index]
    from_char = head["from_char"]
    subsections: list[str] = []

    for following in sections[index + 1:]:
        if following["level"] <= head["level"]:
            return from_char, following["from_char"], subsections
        subsections.append(following["header"])

    return from_char, length, subsections
```

- [ ] **Step 4: Use it in the handler**

In `_doc_get_chapter_handler`, the match loop currently keeps `best_match` as a dict. Track its index instead so `chapter_range` can be called — change `best_match = s` to also record `best_index = i` (iterate with `enumerate(sections)`), then replace the text extraction:

```python
    markdown = db.get_document_markdown(int(doc_id))
    if markdown is None:
        return ToolResult(content="Could not read document text.", is_error=True)

    from_char, to_char, subsections = chapter_range(
        sections, best_index, len(markdown),
    )
    text = markdown[from_char:to_char]

    return ToolResult(
        content=text,
        metadata={
            "header": best_match["header"],
            "from_char": from_char,
            "to_char": to_char,
            "subsections_included": subsections,
        },
    )
```

Import `chapter_range` from `.validation`.

- [ ] **Step 5: Correct the tool description**

In `consensus/tools_document/provider.py`, replace the `doc_get_chapter` description so it matches the behaviour the model now gets:

```python
            description=(
                "Get the full text of a named section/chapter, including "
                "all of its subsections. Uses fuzzy matching on the header "
                "text."
            ),
```

- [ ] **Step 6: Run the tests, then the full suite, then commit**

Run: `uv run pytest tests/test_tools_document_failures.py -k chapter -v`
Then: `uv run pytest -q`

```bash
git add consensus/tools_document/validation.py \
        consensus/tools_document/handlers.py \
        consensus/tools_document/provider.py \
        tests/test_tools_document_failures.py
git commit -m "$(cat <<'EOF'
fix(#78): doc_get_chapter returns the chapter, not just its preamble

extract_sections ends a section at the next header of any level, which
is right for chunk boundaries and wrong for a chapter: an AI asking for
"Methods" silently got its introduction while the tool description
promised the full text. A pure chapter_range helper now runs to the next
header at the same or a higher level; sections_json and chunk boundaries
are untouched, so no document needs re-ingesting.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Split the RAG handlers, update the facade guard and docs

By this point `handlers.py` has grown past the ~500-line limit. Golden rule 8 and the recipe in HANDOVER both say to split at the moment the limit is crossed, not later.

**Files:**
- Create: `consensus/tools_document/handlers_rag.py`
- Modify: `consensus/tools_document/handlers.py`, `consensus/tools_document/provider.py`, `consensus/tools_document/__init__.py`, `tests/test_tools_document_facade.py`
- Modify docs: `CLAUDE.md`, `README.md`, `docs/BUILTIN_TOOLS.md`, `docs/devel/02-architecture.md`, `docs/devel/08-tool-use.md`

- [ ] **Step 1: Measure, and skip the split if it is not needed**

Run: `wc -l consensus/tools_document/handlers.py`
If it is at or under 500, skip to Step 4 — do not split a compliant file for its own sake.

- [ ] **Step 2: Move the two RAG handlers verbatim**

Create `consensus/tools_document/handlers_rag.py` with a module docstring, and move `_doc_ask_handler`, `_doc_summary_handler`, `_reindex_message`, `_post_indexing_notice` and `_notified_index_failures` into it by line range (`sed -n 'a,bp'`) so they transfer byte-identically. Diff each moved range back against `git show HEAD:consensus/tools_document/handlers.py` and confirm every range is identical and every non-blank line of the original is accounted for.

Update `provider.py` to import the two handlers from `.handlers_rag`.

- [ ] **Step 3: Verify with ruff and the suite**

Run: `uvx ruff check --select F consensus/tools_document`
Expected: no unused or undefined names.
Run: `uv run pytest -q`

- [ ] **Step 4: Update the facade guard**

In `tests/test_tools_document_facade.py`, add `"handlers_rag"` to `SUBMODULES` (only if Step 2 ran). `EXPECTED_PUBLIC_API` is unchanged — this work adds no public names — but add a test pinning the new return type, since a caller treating `ParsedDocument` as a `str` is exactly the late failure the guard exists to catch:

```python
def test_parse_document_returns_a_parsed_document():
    """The public parser returns a ParsedDocument, not a bare string.

    Pinned because ingestion reads ``.markdown``: a revert to a plain str
    would fail only when a document is actually ingested.
    """
    from consensus.tools_document.parsing import ParsedDocument

    parsed = tools_document.parse_document(b"hello", "a.txt", "text/plain")
    assert isinstance(parsed, ParsedDocument)
    assert parsed.markdown == "hello"
```

- [ ] **Step 5: Verify the guard actually guards**

Temporarily delete `parse_document` from `__init__.py`'s `__all__` and re-run `uv run pytest tests/test_tools_document_facade.py`. Confirm it FAILS, then restore it. An unverified guard is not a guard.

- [ ] **Step 6: Update the docs that carry module inventories**

The #61 recipe records that these all name package modules and go stale together: `CLAUDE.md` (the `tools_document/` bullet in **Key modules**), `README.md`, `docs/BUILTIN_TOOLS.md`, `docs/devel/02-architecture.md`, `docs/devel/08-tool-use.md`.

Add `errors.py`, `validation.py` and (if created) `handlers_rag.py` to the `tools_document` module list in `CLAUDE.md`. In `docs/BUILTIN_TOOLS.md` and `docs/devel/08-tool-use.md`, correct the `doc_get_chapter` entry to say it includes subsections, and note that `doc_ask` applies a relevance threshold and reports a re-index requirement. Grep for the old promises first:

```bash
grep -rn "doc_get_chapter\|still being indexed\|Empty PDF" docs/ README.md CLAUDE.md
```

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`

```bash
git add consensus/tools_document/ tests/test_tools_document_facade.py \
        CLAUDE.md README.md docs/
git commit -m "$(cat <<'EOF'
refactor(#78,#61): split the RAG handlers and update the docs

handlers.py crossed the 500-line rule with this work, so doc_ask and
doc_summary move to handlers_rag.py at the moment the limit was crossed.
The facade guard gains the new submodule and pins parse_document's
ParsedDocument return type, since a revert to a bare str would fail only
when a document is actually ingested.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Close out the issue

- [ ] **Step 1: Confirm the whole suite is green**

Run: `uv run pytest -q`
Record the passing count — HANDOVER cites it in one place on purpose.

- [ ] **Step 2: Check the line-count table**

Run: `find consensus -name '*.py' | xargs wc -l | sort -rn | head -20`
HANDOVER's issue-#61 table must match `wc -l` exactly; the previous session's figures went stale within two PRs.

- [ ] **Step 3: Update HANDOVER.md and ROADMAP.md**

In `HANDOVER.md`: move issue #78 out of "Open work" into the merged-campaigns summary, record the contracts this work establishes (errors typed at the fault site; `_summary_snippet` needs the status; `chapter_range` is where chapter extent lives, not `extract_sections`; the indexing-failure state is module-level and cleared by a clean pass), refresh the line-count table and the test count. Keep both files under 500 lines — prune finished detail rather than appending.

In `ROADMAP.md`: change the "Make `tools_document` failures visible" row from 📋 Planned to ✅ Done with a one-line summary, and update the test-suite row's count.

- [ ] **Step 4: Commit the docs, push, and open the PR**

```bash
git add HANDOVER.md ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(#78): update HANDOVER and ROADMAP for the failure-visibility work

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin fix/tools-document-failure-visibility-78
gh pr create --base main --title "fix(#78): make tools_document failures visible" --body "..."
```

The PR body must say "Closes #78", summarise the ten defects by theme rather than restating the issue, and tell reviewers what is deliberately *not* here: the summary regeneration path and OCR for scanned PDFs.

---

## Self-Review

**Spec coverage.** §1 → Task 2; §2 → Task 2; §3 → Tasks 3, 4; §4 → Tasks 5, 6; §5 → Tasks 1, 7; §6 → Task 8; §7 → Tasks 7, 9; §8 → Tasks 10, 11; module sizes → Task 12. All ten issue defects map to a task: 1→2/4, 2→1/7, 3→9, 4→8, 5→8, 6→2, 7→5, 8→6, 9→10, 10→11.

**Type consistency.** `ParsedDocument.markdown` (Task 5) is read by ingestion in Task 5 Step 5 and asserted in Task 12 Step 4. `RankingResult.ranked` (Task 8) is used in both handler call sites in the same task. `IndexingFailure.consecutive_failures` and `.last_error` (Task 7) are read by Task 9 Step 4. `_summary_snippet(summary, status)` (Task 4) matches its three call sites. `_spawn_embedding_pass` (Task 1) is the name used in Task 9 Step 4. `resolve_range` and `chapter_range` both live in `validation.py` (Tasks 10, 11).

**Known soft spots flagged inline rather than guessed:** `create_discussion`'s signature (Task 3 Step 1) and `Entity.from_db_row`'s real name (Task 9 Step 3) must be read from the codebase before use.
