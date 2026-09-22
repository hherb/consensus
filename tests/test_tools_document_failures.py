"""Failure-path tests for the document RAG package (issue #78).

Every test here pins a case where a failure used to be indistinguishable
from a success: an error string returned as content, a non-error result,
or a value persisted to the database.
"""

import logging

import pytest

from consensus.tools_document import handlers, ingestion, llm
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

    disc_id = tmp_db.create_discussion("topic", 0)
    tmp_db.add_discussion_document(disc_id, doc_id)
    attached = tmp_db.get_discussion_documents(disc_id)
    assert attached[0]["summary_status"] == "failed"


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
