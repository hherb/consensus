"""Failure-path tests for the document RAG package (issue #78).

Every test here pins a case where a failure used to be indistinguishable
from a success: an error string returned as content, a non-error result,
or a value persisted to the database.
"""

import logging

import httpx
import pytest

from consensus.tools_document import embedding, handlers, ingestion, llm, parsing
from consensus.tools_document.errors import (
    DocumentError, DocumentInterpretationError, DocumentParseError,
)
from consensus.tools import ToolContext
from tests.document_helpers import patch_where_defined


async def _no_sleep(_seconds):
    """Collapse backoff delays so retry tests stay fast."""
    return None


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


@pytest.mark.asyncio
async def test_library_search_reports_a_failed_summary(tmp_db, sample_ai_entity):
    """Semantic search must not hide a failed summary behind 'ok'.

    Regression test for the ``seen_docs`` dict built inside
    ``_doc_list_handler``'s ``full_library and query`` (semantic search)
    branch: that dict is assembled from selected fields of the raw document
    row rather than the row itself, and it originally omitted
    ``summary_status``, so ``doc.get("summary_status", SUMMARY_STATUS_OK)``
    would silently fall back to ``'ok'`` for every document found by search
    — the one ``doc_list`` mode where a failed summary could still read as
    a plain empty one instead of "(summary unavailable — generation
    failed)" (issue #78 defect 1).
    """
    from consensus.tools_document import chunking
    from consensus.tools_document.constants import SUMMARY_STATUS_FAILED
    from tests.document_helpers import FakeEmbedClient, embed_all

    markdown = "# Broken\n\nBody text about widgets."
    doc_id = tmp_db.add_document(
        filename="broken.md", title="Broken", summary="",
        mime_type="text/markdown", source_type="text", source_url=None,
        markdown=markdown, char_count=len(markdown), sections_json="[]",
        summary_status=SUMMARY_STATUS_FAILED,
    )
    for chunk in chunking.chunk_document(markdown, chunk_size=60, overlap=0):
        tmp_db.add_document_chunk(
            doc_id, chunk["chunk_index"], chunk["content"],
            chunk["from_char"], chunk["to_char"], chunk.get("section_header"),
        )
    embed_all(tmp_db, doc_id, (1.0, 0.0))

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers._doc_list_handler(
        {"full_library": True, "query": "widgets"},
        context, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
    )
    assert f"[ID {doc_id}] Broken" in result.content
    assert "summary unavailable" in result.content


def test_image_only_pdf_raises_instead_of_returning_placeholder(monkeypatch):
    """A scanned PDF must not ingest as the string "(Empty PDF)".

    It was 11 non-blank characters, so the "empty after parsing" guard let
    it through; doc_ask then answered questions from it.

    Uses ``monkeypatch.setitem(sys.modules, ...)`` rather than manual
    ``sys.modules`` mutation with a ``try``/``finally`` — pytest unwinds it
    automatically even if the assertion fails, so a broken test can't leak a
    fake ``PyPDF2`` module into the rest of the suite.
    """
    import sys
    import types

    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        pages = [FakePage()]

        def __init__(self, *args):
            pass

    fake = types.ModuleType("PyPDF2")
    fake.PdfReader = FakeReader
    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    monkeypatch.setitem(sys.modules, "PyPDF2", fake)

    with pytest.raises(DocumentParseError) as exc:
        parsing.parse_document(b"%PDF-1.4 fake", "scan.pdf",
                               "application/pdf")
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
