"""Failure-path tests for the document RAG package (issue #78).

Every test here pins a case where a failure used to be indistinguishable
from a success: an error string returned as content, a non-error result,
or a value persisted to the database.
"""

import logging

import httpx
import pytest

from consensus.tools_document import (
    embedding, handlers, handlers_rag, ingestion, llm, parsing,
)
from consensus.tools_document.embedding import _pack_embedding
from consensus.tools_document.errors import (
    DocumentError, DocumentInterpretationError, DocumentParseError,
)
from consensus.tools import ToolContext
from consensus.models import Discussion, Entity, EntityType, MessageRole
from tests.document_helpers import image_only_pdf_bytes, patch_where_defined


async def _no_sleep(_seconds):
    """Collapse backoff delays so retry tests stay fast."""
    return None


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


def test_image_only_pdf_raises_instead_of_returning_placeholder():
    """A scanned PDF must not ingest as the string "(Empty PDF)".

    It was 11 non-blank characters, so the "empty after parsing" guard let
    it through; doc_ask then answered questions from it.

    Deliberately patches *nothing*: this runs against the real installed
    configuration, which is pdfplumber present and PyPDF2 absent (only
    pdfplumber is a declared dependency). The previous version of this
    test injected a fake ``PyPDF2`` into ``sys.modules``, so the one test
    proving this branch's flagship user-visible behaviour passed only in a
    configuration nobody has: in the real one, PyPDF2's ``ImportError``
    won and the user was told to install pdfplumber — which they already
    had (issue #78 whole-branch review).
    """
    with pytest.raises(DocumentParseError) as exc:
        parsing.parse_document(
            image_only_pdf_bytes(), "scan.pdf", "application/pdf")
    message = str(exc.value).lower()
    assert "scanned" in message
    assert "ocr" in message
    assert "uv pip install" not in message


def test_pdf_with_no_backend_installed_asks_for_an_install(monkeypatch):
    """With *no* PDF library importable, the install hint is the right one.

    The counterpart to the test above: the "install pdfplumber or PyPDF2"
    message is correct only here, when nothing could be imported at all.
    """
    import sys

    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    monkeypatch.setitem(sys.modules, "PyPDF2", None)

    with pytest.raises(DocumentParseError) as exc:
        parsing.parse_document(
            image_only_pdf_bytes(), "scan.pdf", "application/pdf")
    message = str(exc.value)
    assert "requires pdfplumber or PyPDF2" in message
    assert "uv pip install pdfplumber" in message


def test_corrupt_pdf_is_reported_as_unreadable(monkeypatch):
    """An importable backend that cannot read the bytes is a third case.

    Neither "OCR it" nor "install a library": the file is corrupt or
    password-protected, and the message must say so.
    """
    import sys
    import types

    broken = types.ModuleType("pdfplumber")

    def _boom(_stream):
        raise ValueError("EOF marker not found")

    broken.open = _boom
    monkeypatch.setitem(sys.modules, "pdfplumber", broken)
    monkeypatch.setitem(sys.modules, "PyPDF2", None)

    with pytest.raises(DocumentParseError) as exc:
        parsing.parse_document(b"%PDF-1.4 truncated", "x.pdf",
                               "application/pdf")
    message = str(exc.value)
    assert "EOF marker not found" in message
    assert "corrupt or password-protected" in message


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
    assert parsed.notes == ()


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


class _FakeStream:
    """The async context manager ``_FakeAsyncClient.stream`` returns.

    ``fetch_url_content`` reads bodies with ``client.stream(...)`` so the
    size cap can abort an oversized transfer instead of measuring it after
    the fact, so the scripted outcome is delivered (or raised) on entry.
    """

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client._next_outcome()

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    """Scripted httpx.AsyncClient replacement for fetch_url_content."""

    def __init__(self, outcomes, **kwargs):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        """Return the next scripted outcome as a streaming context."""
        return _FakeStream(self)

    def _next_outcome(self):
        """Pop the next scripted response, raising a scripted exception."""
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ChunkedResponse:
    """A streaming response with no ``content-length``, as chunked transfer.

    ``httpx.Response(content=...)`` always sets ``content-length``, so it
    cannot exercise the streaming size cap at all — the very case the cap
    exists for, since a header-less body is the one that would otherwise be
    read fully into memory before being measured.
    """

    status_code = 200
    headers = {"content-type": "text/plain"}

    def __init__(self, chunk: bytes, chunks: int) -> None:
        self._chunk = chunk
        self._chunks = chunks
        self.yielded = 0

    def raise_for_status(self) -> None:
        """No-op: this fake is always a 200."""

    async def aiter_bytes(self):
        """Yield up to *chunks* copies of the body chunk, counting them."""
        for _ in range(self._chunks):
            self.yielded += 1
            yield self._chunk


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
    """A header-less oversized body is aborted *while* it streams.

    ``await client.get(url)`` buffered the whole body and only then
    measured it, so a chunked multi-gigabyte URL passed to ``doc_add``
    OOM'd the process — the guard that exists to prevent an OOM caused
    one (issue #78 whole-branch review). The chunk count proves the
    transfer stopped early rather than being read to the end.
    """
    from consensus.tools_document.constants import MAX_DOCUMENT_BYTES

    chunk_size = 1024 * 1024
    chunks_at_the_cap = MAX_DOCUMENT_BYTES // chunk_size
    response = _ChunkedResponse(b"x" * chunk_size, chunks=chunks_at_the_cap * 100)
    assert "content-length" not in response.headers

    monkeypatch.setattr(
        parsing.httpx, "AsyncClient",
        lambda **kwargs: _FakeAsyncClient([response]),
    )
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    with pytest.raises(DocumentParseError) as exc:
        await parsing.fetch_url_content("https://example.com/big")

    assert "too large" in str(exc.value).lower()
    assert response.yielded == chunks_at_the_cap + 1


@pytest.mark.asyncio
async def test_fetch_gives_up_after_the_retry_budget(monkeypatch):
    """Transient failures are retried exactly URL_FETCH_MAX_RETRIES times.

    Golden rule 5's central contract: retry with backoff, then stop and
    say so rather than retrying forever or failing on the first blip.
    """
    from consensus.tools_document.constants import URL_FETCH_MAX_RETRIES

    made = {}

    def factory(**kwargs):
        made["client"] = _FakeAsyncClient([
            httpx.ConnectError("refused"),
            httpx.TimeoutException("slow"),
            httpx.ConnectError("refused again"),
        ])
        return made["client"]

    monkeypatch.setattr(parsing.httpx, "AsyncClient", factory)
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    with pytest.raises(DocumentParseError) as exc:
        await parsing.fetch_url_content("https://example.com/flaky")

    assert made["client"].calls == URL_FETCH_MAX_RETRIES == 3
    message = str(exc.value)
    assert f"after {URL_FETCH_MAX_RETRIES} attempts" in message
    assert "refused again" in message


@pytest.fixture(autouse=True)
def _clear_indexing_state():
    """Module-level indexing state must not leak between tests."""
    yield
    embedding._indexing_failures.clear()
    embedding._embedding_docs.clear()
    handlers_rag._notified_index_failures.clear()


@pytest.mark.asyncio
async def test_pass_crash_is_logged_not_swallowed(tmp_db, caplog):
    """A crash inside the pass logs a traceback and records a failure."""
    class ExplodingDb:
        # A locked database still has a path; doc_key requires one, because
        # defaulting it to "" produces a key that collides across sessions.
        db_path = "/tmp/exploding.db"

        def get_document_chunks(self, doc_id):
            raise RuntimeError("database is locked")

    db = ExplodingDb()
    with caplog.at_level(logging.ERROR):
        await embedding._embed_document_chunks(7, db, object())

    assert "database is locked" in caplog.text
    failure = embedding.get_indexing_failure(db, 7)
    assert failure is not None
    assert failure.consecutive_failures == 1
    assert "database is locked" in failure.last_error
    assert embedding.doc_key(db, 7) not in embedding._embedding_docs


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

    failure = embedding.get_indexing_failure(tmp_db, doc_id)
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
    assert embedding.get_indexing_failure(
        tmp_db, doc_id).consecutive_failures == 2

    await embedding._embed_document_chunks(
        doc_id, tmp_db, FakeEmbedClient(vector=[1.0, 0.0, 0.0]))
    assert embedding.get_indexing_failure(tmp_db, doc_id) is None


# ---------------------------------------------------------------------------
# Task 8: relevance floor and dimension-mismatch reporting
# ---------------------------------------------------------------------------

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
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "what?"},
        context, tmp_db, client, None,
    )

    assert result.is_error
    assert "re-indexed" in result.content


@pytest.mark.asyncio
async def test_doc_ask_reports_no_relevant_passage_below_threshold(
    tmp_db, sample_ai_entity,
):
    """A same-dimension but unrelated embedding reads as 'not relevant',
    not as a dimension mismatch — the two used to be indistinguishable
    because the default threshold of 0.0 let every row through."""
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="g.md", title="G", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# G\n\nBody.",
        char_count=9, sections_json="[]",
    )
    chunk_id = tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    tmp_db.set_chunk_embedding(chunk_id, _pack_embedding([0.0, 1.0, 0.0]))

    client = FakeEmbedClient(vector=[1.0, 0.0, 0.0])
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "what?"},
        context, tmp_db, client, None,
    )

    assert not result.is_error
    assert "is relevant" in result.content.lower()
    assert "re-indexed" not in result.content


@pytest.mark.asyncio
async def test_doc_ask_reports_an_interpretation_failure_explicitly(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """doc_ask names the model, provider and document id on an LLM failure.

    Previously ``DocumentInterpretationError`` fell through to
    ``ToolRegistry``'s generic ``except Exception``, which produces a bare
    "Tool error: ..." message and always logs a full traceback — even
    though a failing provider here is an expected, not exceptional, case.
    """
    from tests.document_helpers import FakeApp, FakeEmbedClient, embed_all

    doc_id = tmp_db.add_document(
        filename="f.md", title="F", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# F\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embed_all(tmp_db, doc_id, (1.0, 0.0))

    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("401 Unauthorized")

    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_call_interpretation_llm", boom,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "what?"},
        context, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
    )

    assert result.is_error
    assert str(doc_id) in result.content
    assert "test-model" in result.content
    assert "TestProvider" in result.content
    assert "401 Unauthorized" in result.content


@pytest.mark.asyncio
async def test_doc_ask_interpretation_failure_does_not_log_a_traceback(
    tmp_db, sample_ai_entity, monkeypatch, caplog,
):
    """The explicit catch logs a warning, not ``logger.exception``.

    A provider failure here is expected and already reported to the
    caller; a full traceback for every such failure is noise ToolRegistry's
    generic handler used to produce.
    """
    from tests.document_helpers import FakeApp, FakeEmbedClient, embed_all

    doc_id = tmp_db.add_document(
        filename="h.md", title="H", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# H\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embed_all(tmp_db, doc_id, (1.0, 0.0))

    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("401 Unauthorized")

    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_call_interpretation_llm", boom,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    # At WARNING, not ERROR: the code logs at WARNING, so an ERROR-level
    # capture left caplog.text empty and the assertion below vacuously true
    # — it passed even against a handler that logged nothing at all.
    with caplog.at_level(logging.WARNING):
        await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "what?"},
            context, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )

    records = [r for r in caplog.records if "interpretation failed" in r.message]
    assert records, "the failure must be logged"
    assert all(r.levelno == logging.WARNING for r in records)
    # exc_info is what turns a warning into a traceback; assert its absence
    # directly rather than grepping the rendered text.
    assert all(r.exc_info is None for r in records)
    assert "Traceback" not in caplog.text


# ---------------------------------------------------------------------------
# Task 9: a dead embedder stops reporting itself as a delay
# ---------------------------------------------------------------------------

class _NoticeApp:
    """App stand-in carrying a real Discussion so ``post_notice`` can run."""

    def __init__(self, db, moderator: Entity, discussion: Discussion) -> None:
        """Store the collaborators ``_post_indexing_notice`` needs.

        Args:
            db: A real database handle (the ``tmp_db`` fixture).
            moderator: The discussion's moderator entity.
            discussion: The in-memory discussion the notice is appended to.
        """
        self.db = db
        self.discussion = discussion
        self._moderator = moderator

    def _resolve_key_for_moderator(self, provider_id, env_name):
        """Return a dummy key; unused by these notice-only tests."""
        return "k"


@pytest.fixture
def notice_app(tmp_db):
    """An app whose discussion can receive an indexing-failure notice."""
    mod_id = tmp_db.add_entity(
        "Mod", "human", "#00ff00", None, "", 0.5, 512, "")
    disc_id = tmp_db.create_discussion("topic", mod_id)
    discussion = Discussion(id=disc_id, topic="topic", moderator_id=mod_id)
    moderator = Entity(
        name="Mod", entity_type=EntityType.HUMAN, id=mod_id,
        avatar_color="#00ff00",
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
    result = await handlers_rag._doc_ask_handler(
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
        tmp_db, doc_id,
        "Cannot connect to embedding service at localhost:11434")

    context = ToolContext(caller_entity_id=0, discussion_id=notice_app.discussion.id)
    result = await handlers_rag._doc_ask_handler(
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
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")

    context = ToolContext(caller_entity_id=0, discussion_id=notice_app.discussion.id)
    for _ in range(3):
        await handlers_rag._doc_ask_handler(
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
        """App stand-in with no live discussion to notify."""

        def __init__(self, db):
            self.db = db
            self.discussion = None

    doc_id = tmp_db.add_document(
        filename="i.md", title="I", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# I\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")

    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), NoDiscussionApp(tmp_db),
    )
    assert result.is_error
    assert "embedder down" in result.content


@pytest.mark.asyncio
async def test_new_failure_streak_after_recovery_posts_a_second_notice(
    tmp_db, notice_app,
):
    """A document that fails, recovers, then fails again earns a fresh notice.

    Without evicting ``doc_id`` from ``_notified_index_failures`` once the
    document is observed healthy again, "one notice per failure streak"
    silently degrades into "one notice per document ever" — the human
    would never learn about a second, genuinely new outage (issue #78
    task 9 follow-up).
    """
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="j.md", title="J", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# J\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    context = ToolContext(
        caller_entity_id=0, discussion_id=notice_app.discussion.id)

    # First failure streak: one notice.
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")
    await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    # Recover: the failure record clears and doc_ask next observes the
    # document as healthy (still genuinely indexing, but with no recorded
    # failure). embed_client=None so no background pass is spawned — this
    # call only needs to exercise the healthy path's eviction.
    embedding._clear_indexing_failure(tmp_db, doc_id)
    await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, None, notice_app,
    )

    # A brand new failure streak begins.
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down again")
    await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    notices = [
        m for m in notice_app.discussion.messages
        if m.role == MessageRole.SYSTEM
        and "could not be indexed" in m.content
    ]
    assert len(notices) == 2


# ---------------------------------------------------------------------------
# Whole-branch review: a recorded failure must stay recoverable, and the
# process-global bookkeeping must not conflate two sessions' documents.
# ---------------------------------------------------------------------------

def _unindexed_doc(db, filename: str = "retry.md") -> int:
    """Store a one-chunk document with no embeddings and return its id."""
    doc_id = db.add_document(
        filename=filename, title=filename, summary="",
        mime_type="text/markdown", source_type="upload", source_url=None,
        markdown="# R\n\nBody.", char_count=9, sections_json="[]",
    )
    db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    return doc_id


@pytest.mark.asyncio
async def test_a_stale_indexing_failure_is_retried(
    tmp_db, notice_app, monkeypatch,
):
    """A recorded failure must not condemn the document forever.

    The embedding service being down for thirty seconds during ingestion
    used to make every later ``doc_ask`` on that document return an error
    for the rest of the process lifetime, because the failure branch
    returned before the re-kick and nothing else ever cleared the record.
    ``IndexingFailure.last_attempt`` being written and never read was the
    tell (issue #78 whole-branch review).
    """
    from consensus.tools_document.constants import INDEXING_RETRY_INTERVAL
    from tests.document_helpers import FakeEmbedClient

    spawned = []
    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_spawn_embedding_pass",
        lambda doc_id, db, embed_client: spawned.append(doc_id),
    )

    doc_id = _unindexed_doc(tmp_db)
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")
    # Age the recorded attempt past the retry interval.
    failure = embedding.get_indexing_failure(tmp_db, doc_id)
    failure.last_attempt -= INDEXING_RETRY_INTERVAL + 1

    context = ToolContext(
        caller_entity_id=0, discussion_id=notice_app.discussion.id)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    assert spawned == [doc_id]
    # The honest error is still reported; the retry is additional.
    assert result.is_error
    assert "embedder down" in result.content
    assert "fresh indexing attempt" in result.content


@pytest.mark.asyncio
async def test_a_recent_indexing_failure_is_not_retried(
    tmp_db, notice_app, monkeypatch,
):
    """A service that is still down must not be hammered every call.

    ``doc_ask`` is retried up to MAX_TOOL_ITERATIONS times per turn, so
    the retry is gated on ``INDEXING_RETRY_INTERVAL``.
    """
    from tests.document_helpers import FakeEmbedClient

    spawned = []
    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_spawn_embedding_pass",
        lambda doc_id, db, embed_client: spawned.append(doc_id),
    )

    doc_id = _unindexed_doc(tmp_db)
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")

    context = ToolContext(
        caller_entity_id=0, discussion_id=notice_app.discussion.id)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    assert spawned == []
    assert result.is_error
    assert "embedder down" in result.content
    assert "fresh indexing attempt" not in result.content


@pytest.mark.asyncio
async def test_a_retry_does_not_stack_on_an_in_flight_pass(
    tmp_db, notice_app, monkeypatch,
):
    """The in-flight marker still wins over the retry interval."""
    from consensus.tools_document.constants import INDEXING_RETRY_INTERVAL
    from tests.document_helpers import FakeEmbedClient

    spawned = []
    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_spawn_embedding_pass",
        lambda doc_id, db, embed_client: spawned.append(doc_id),
    )

    doc_id = _unindexed_doc(tmp_db)
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder down")
    embedding.get_indexing_failure(
        tmp_db, doc_id).last_attempt -= INDEXING_RETRY_INTERVAL + 1
    embedding._embedding_docs.add(embedding.doc_key(tmp_db, doc_id))

    context = ToolContext(
        caller_entity_id=0, discussion_id=notice_app.discussion.id)
    await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )

    assert spawned == []


@pytest.mark.asyncio
async def test_two_databases_with_the_same_doc_id_do_not_collide(
    tmp_db, tmp_path, notice_app,
):
    """Session A's broken document must not break session B's healthy one.

    In ``--multi-user`` mode every session gets its own SQLite file, so
    every session's first document is id 1. Keyed by id alone, one
    session's failed indexing pass made another session's ``doc_ask``
    report "Indexing failed" *and* post a fabricated notice into its
    discussion (issue #78 whole-branch review).
    """
    from consensus.db import Database

    other = Database(str(tmp_path / "other-session.db"))
    try:
        doc_a = _unindexed_doc(tmp_db, "a.md")
        doc_b = _unindexed_doc(other, "b.md")
        assert doc_a == doc_b == 1

        embedding._record_indexing_failure(tmp_db, doc_a, "embedder down")

        assert embedding.get_indexing_failure(other, doc_b) is None

        context = ToolContext(
            caller_entity_id=0, discussion_id=notice_app.discussion.id)
        # embed_client=None: this call only needs to prove that B's
        # document is *seen* as healthy, not to actually index it.
        result = await handlers_rag._doc_ask_handler(
            {"document_id": doc_b, "question": "q"},
            context, other, None, notice_app,
        )

        assert not result.is_error
        assert "still being indexed" in result.content
        assert not [
            m for m in notice_app.discussion.messages
            if "could not be indexed" in m.content
        ]
    finally:
        other.conn.close()


# ---------------------------------------------------------------------------
# Task 10: range validation
# ---------------------------------------------------------------------------

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
        filename="j2.md", title="J2", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None,
        markdown="# J\n\n" + "x" * 1000, char_count=1005, sections_json="[]",
    )
    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers._doc_get_text_handler(
        {"document_id": doc_id, "from_char": 500, "to_char": 100},
        context, tmp_db, None, None,
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_doc_summary_reports_an_interpretation_failure_explicitly(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """doc_summary names the model, provider and document id on an LLM
    failure, matching the treatment doc_ask already received.

    Previously ``DocumentInterpretationError`` fell through to
    ``ToolRegistry``'s generic ``except Exception``, which produces a bare
    "Tool error: ..." message and always logs a full traceback — even
    though a failing provider here is an expected, not exceptional, case.
    """
    from tests.document_helpers import FakeApp

    doc_id = tmp_db.add_document(
        filename="summ1.md", title="Summ1", summary="",
        mime_type="text/markdown", source_type="upload", source_url=None,
        markdown="# Summ1\n\nSome body text.", char_count=27,
        sections_json="[]",
    )

    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("401 Unauthorized")

    patch_where_defined(
        monkeypatch, handlers_rag._doc_summary_handler,
        "_call_interpretation_llm", boom,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers_rag._doc_summary_handler(
        {"document_id": doc_id}, context, tmp_db, None, FakeApp(tmp_db),
    )

    assert result.is_error
    assert str(doc_id) in result.content
    assert "test-model" in result.content
    assert "TestProvider" in result.content
    assert "401 Unauthorized" in result.content


@pytest.mark.asyncio
async def test_doc_summary_interpretation_failure_does_not_log_a_traceback(
    tmp_db, sample_ai_entity, monkeypatch, caplog,
):
    """The explicit catch logs a warning, not ``logger.exception``."""
    from tests.document_helpers import FakeApp

    doc_id = tmp_db.add_document(
        filename="summ2.md", title="Summ2", summary="",
        mime_type="text/markdown", source_type="upload", source_url=None,
        markdown="# Summ2\n\nSome body text.", char_count=27,
        sections_json="[]",
    )

    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("401 Unauthorized")

    patch_where_defined(
        monkeypatch, handlers_rag._doc_summary_handler,
        "_call_interpretation_llm", boom,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    # WARNING, not ERROR — see the doc_ask twin: an ERROR capture made this
    # assertion vacuous, since the code under test logs at WARNING.
    with caplog.at_level(logging.WARNING):
        await handlers_rag._doc_summary_handler(
            {"document_id": doc_id}, context, tmp_db, None, FakeApp(tmp_db),
        )

    records = [r for r in caplog.records if "summary" in r.message.lower()]
    assert records, "the failure must be logged"
    assert all(r.levelno == logging.WARNING for r in records)
    assert all(r.exc_info is None for r in records)
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_doc_summary_map_reduce_reports_interpretation_failure(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """The map-reduce path (long documents) is covered too.

    ``_doc_summary_handler`` has three separate ``_call_interpretation_llm``
    call sites — the direct path and two inside map-reduce (per-chunk and
    the final combine). A failure at the first chunk-summary call site must
    be reported the same way as a failure on the direct path.
    """
    from tests.document_helpers import FakeApp
    from consensus.tools_document.constants import SUMMARY_CHUNK_LIMIT

    long_text = "# Summ3\n\n" + ("word " * (SUMMARY_CHUNK_LIMIT))
    doc_id = tmp_db.add_document(
        filename="summ3.md", title="Summ3", summary="",
        mime_type="text/markdown", source_type="upload", source_url=None,
        markdown=long_text, char_count=len(long_text), sections_json="[]",
    )
    assert len(long_text) > SUMMARY_CHUNK_LIMIT

    async def boom(*args, **kwargs):
        raise DocumentInterpretationError("rate limited")

    patch_where_defined(
        monkeypatch, handlers_rag._doc_summary_handler,
        "_call_interpretation_llm", boom,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers_rag._doc_summary_handler(
        {"document_id": doc_id}, context, tmp_db, None, FakeApp(tmp_db),
    )

    assert result.is_error
    assert str(doc_id) in result.content
    assert "test-model" in result.content
    assert "TestProvider" in result.content
    assert "rate limited" in result.content


# ---------------------------------------------------------------------------
# Task 11: chapters carry their subsections
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Whole-branch review follow-ups: gaps the mutation pass found, plus the
# defects the review itself turned up (see HANDOVER for the catalogue).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doc_summary_rejects_an_inverted_range(tmp_db):
    """doc_summary validated its range, but nothing pinned that it did.

    Replacing ``resolve_range`` here with a lenient clamp left the whole
    suite green while ``doc_summary(from_char=500, to_char=100)`` returned
    "Selected range is empty." as a *non-error* — defect 5 verbatim, on the
    one call site validation.py's own docstring calls historically guarded.
    """
    doc_id = tmp_db.add_document(
        filename="inv.md", title="Inv", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None,
        markdown="# Inv\n\n" + "y" * 1000, char_count=1007, sections_json="[]",
    )
    context = ToolContext(caller_entity_id=0, discussion_id=0)
    result = await handlers_rag._doc_summary_handler(
        {"document_id": doc_id, "from_char": 500, "to_char": 100},
        context, tmp_db, None, None,
    )
    assert result.is_error
    assert "empty" not in result.content.lower()


def test_nul_free_binary_is_rejected_as_mojibake():
    """The replacement-character guard was unreachable from the tests.

    The only test claiming to cover it fed JPEG bytes containing a NUL, so
    it short-circuited on the earlier binary check and ``if False:`` in
    place of the ratio comparison broke nothing. UTF-16 text carries no NUL
    in this sample but decodes to mostly U+FFFD.
    """
    payload = b"\xc3\x28" * 200  # invalid UTF-8, no NUL byte
    assert b"\x00" not in payload
    with pytest.raises(DocumentParseError, match="looks binary"):
        parsing.parse_document(payload, "weird.txt", "text/plain")


def test_binary_served_as_html_is_rejected_too():
    """_parse_html used to skip the guard entirely and tag-strip mojibake."""
    payload = b"\xc3\x28" * 200
    with pytest.raises(DocumentParseError, match="looks binary"):
        parsing.parse_document(payload, "weird.html", "text/html")


def test_parsed_document_rejects_an_unknown_fidelity():
    """A typo'd fidelity compares unequal to *both* constants."""
    with pytest.raises(ValueError, match="fidelity"):
        parsing.ParsedDocument(markdown="x", fidelity="ful")


def test_parsed_document_rejects_degraded_without_a_reason():
    """"Degraded" with no note is a warning nobody can act on."""
    with pytest.raises(ValueError, match="why"):
        parsing.ParsedDocument(markdown="x", fidelity="degraded")


def test_parsed_document_notes_are_immutable():
    """frozen=True blocks rebinding, not list mutation."""
    parsed = parsing.ParsedDocument(markdown="x")
    with pytest.raises(AttributeError):
        parsed.notes.append("sneaky")


def test_doc_key_requires_a_db_path():
    """Defaulting the path to "" produced a cross-session collision."""
    class Pathless:
        pass

    with pytest.raises(ValueError, match="db_path"):
        embedding.doc_key(Pathless(), 1)


def test_summary_snippet_distinguishes_pending_from_absent():
    """'pending' means nobody tried; it read as 'had nothing to say'."""
    assert "generated" in handlers._summary_snippet("", "pending")
    assert handlers._summary_snippet("", "pending") != \
        handlers._summary_snippet("", "ok")
    assert handlers._summary_snippet("", "pending") != \
        handlers._summary_snippet("", "failed")


def test_add_document_rejects_an_unknown_summary_status(tmp_db):
    """SQLite cannot CHECK an added column, so the INSERT path enforces it."""
    with pytest.raises(ValueError, match="summary_status"):
        tmp_db.add_document(
            filename="bad.md", title="Bad", summary="", mime_type="text/md",
            source_type="upload", source_url=None, markdown="x",
            char_count=1, sections_json="[]",
            summary_status="(LLM call failed: boom)",
        )


def test_update_document_summary_writes_the_status_too(tmp_db):
    """Updating text without the status leaves a stale 'failed'/'ok'."""
    doc_id = tmp_db.add_document(
        filename="u.md", title="U", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="x", char_count=1,
        sections_json="[]", summary_status="failed",
    )
    tmp_db.update_document_summary(doc_id, "A real summary.")
    doc = tmp_db.get_document(doc_id)
    assert doc["summary"] == "A real summary."
    assert doc["summary_status"] == "ok"


@pytest.mark.asyncio
async def test_empty_completion_raises_instead_of_summarising_nothing(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """An empty choice used to store summary_status='ok' with no summary."""
    from tests.document_helpers import FakeApp

    class EmptyClient:
        def __init__(self, *a, **kw):
            pass

        async def complete(self, **kwargs):
            class R:
                content = "   "
            return R()

        async def close(self):
            pass

    patch_where_defined(
        monkeypatch, llm._call_interpretation_llm, "AIClient", EmptyClient,
    )
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    with pytest.raises(DocumentInterpretationError, match="empty response"):
        await llm._call_interpretation_llm(
            FakeApp(tmp_db), context, system_prompt="s", user_prompt="u",
        )


@pytest.mark.asyncio
async def test_embedder_error_reaches_the_recorded_failure(tmp_db):
    """The detail named a count, never the cause.

    "12/12 chunks could not be embedded" tells the user nothing to act on;
    the embedder's own message names the endpoint that is down.
    """
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="e.md", title="E", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# E\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)

    client = FakeEmbedClient(error=RuntimeError(
        "Cannot connect to embedding service at http://localhost:11434",
    ))
    await embedding._embed_document_chunks(doc_id, tmp_db, client)

    failure = embedding.get_indexing_failure(tmp_db, doc_id)
    assert failure is not None
    assert "localhost:11434" in failure.last_error
    # and the hint the typed error carries
    assert "embedding service" in failure.last_error.lower()


@pytest.mark.asyncio
async def test_partial_dimension_mismatch_is_reported(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """A *partial* mismatch answered from the survivors and said nothing.

    skipped_dim_mismatch was consulted only when nothing ranked at all, so
    a document holding two embedding dimensions answered from whichever
    chunks matched, with no hint that the rest were excluded.
    """
    from tests.document_helpers import FakeApp, FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="p.md", title="P", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# P\n\nBody.",
        char_count=9, sections_json="[]",
    )
    good = tmp_db.add_document_chunk(doc_id, 0, "Matching text.", 0, 14, None)
    stale = tmp_db.add_document_chunk(doc_id, 1, "Stale text.", 14, 25, None)
    tmp_db.set_chunk_embedding(good, _pack_embedding([1.0, 0.0]))
    # Three dimensions: embedded before the model changed.
    tmp_db.set_chunk_embedding(stale, _pack_embedding([1.0, 0.0, 0.0]))

    async def answer(*args, **kwargs):
        return "An answer from the surviving passage."

    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler,
        "_call_interpretation_llm", answer,
    )

    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "what?"},
        context, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
    )
    assert not result.is_error
    assert "incomplete_retrieval" in result.content
    assert "re-index" in result.content.lower()


def test_migration_backfills_pre_78_error_summaries(tmp_db):
    """015's DEFAULT 'ok' marked the defect-1 rows as having a good summary.

    Those rows' summary column literally holds the old helper's error
    string, so doc_list kept reprinting an LLM error to every participant.
    Exercised through the real migrator, not by running the SQL by hand.
    """
    from consensus import migrator

    # A row as the pre-#78 code would have written it.
    tmp_db.conn.execute(
        "INSERT INTO documents (filename, title, summary, mime_type, "
        "source_type, source_url, markdown, char_count, sections_json, "
        "created_at, summary_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("old.md", "Old", "(LLM call failed: 401 Unauthorized)",
         "text/markdown", "upload", None, "# Old", 5, "[]", 0.0, "ok"),
    )
    tmp_db.conn.execute(
        "INSERT INTO documents (filename, title, summary, mime_type, "
        "source_type, source_url, markdown, char_count, sections_json, "
        "created_at, summary_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("fine.md", "Fine", "A genuine summary.", "text/markdown",
         "upload", None, "# Fine", 6, "[]", 0.0, "ok"),
    )
    # Rewind 016 so the real migrator re-applies it over those rows.
    tmp_db.conn.execute("DELETE FROM migrations WHERE version=016")
    tmp_db.conn.commit()
    migrator._migrations_done.discard(tmp_db.db_path)
    migrator.run_migrations(tmp_db.conn, tmp_db._lock, tmp_db.db_path)

    rows = {
        r[0]: (r[1], r[2]) for r in tmp_db.conn.execute(
            "SELECT filename, summary, summary_status FROM documents"
        ).fetchall()
    }
    assert rows["old.md"] == ("", "failed")
    # A real summary is left alone.
    assert rows["fine.md"] == ("A genuine summary.", "ok")


@pytest.mark.asyncio
async def test_fetch_retries_a_500(monkeypatch):
    """The docstring advertises 5xx retries; only 4xx no-retry was pinned."""
    request = httpx.Request("GET", "https://example.com/a.txt")
    server_error = httpx.Response(500, request=request)

    made = {}

    def factory(**kwargs):
        client = _FakeAsyncClient([server_error, _ok_response()])
        made["client"] = client
        return client

    monkeypatch.setattr(parsing.httpx, "AsyncClient", factory)
    monkeypatch.setattr(parsing.asyncio, "sleep", _no_sleep)

    content, _filename, _mime = await parsing.fetch_url_content(
        "https://example.com/a.txt")
    assert content == b"hello"
    assert made["client"].calls == 2


@pytest.mark.asyncio
async def test_fetch_backoff_is_exponential(monkeypatch):
    """"Exponential backoff" (golden rule 5) was claimed but not asserted.

    Every fetch test patches sleep to a no-op, so a change to a fixed 1s
    delay — or to 2**attempt without the base — passed unnoticed.
    """
    from consensus.tools_document.constants import (
        URL_FETCH_BASE_DELAY, URL_FETCH_MAX_RETRIES,
    )

    delays = []

    async def record(seconds):
        delays.append(seconds)

    def factory(**kwargs):
        return _FakeAsyncClient(
            [httpx.TimeoutException("slow")] * URL_FETCH_MAX_RETRIES,
        )

    monkeypatch.setattr(parsing.httpx, "AsyncClient", factory)
    monkeypatch.setattr(parsing.asyncio, "sleep", record)

    with pytest.raises(DocumentParseError):
        await parsing.fetch_url_content("https://example.com/a.txt")

    assert delays == [
        URL_FETCH_BASE_DELAY * 2 ** i for i in range(len(delays))
    ]
    assert delays == sorted(delays) and len(delays) >= 2


@pytest.mark.asyncio
async def test_a_raising_post_notice_does_not_break_the_tool_call(
    tmp_db, notice_app, monkeypatch,
):
    """The reporter of last resort must not raise one of its own.

    The guard's docstring promises this (the lesson of issue #74), but the
    only test exercised ``discussion is None``, which returns *before* the
    risky call — so ``except Exception: raise`` broke nothing.
    """
    from tests.document_helpers import FakeEmbedClient

    doc_id = tmp_db.add_document(
        filename="n.md", title="N", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown="# N\n\nBody.",
        char_count=9, sections_json="[]",
    )
    tmp_db.add_document_chunk(doc_id, 0, "Body.", 0, 5, None)
    embedding._record_indexing_failure(tmp_db, doc_id, "embedder is down")

    def boom(*args, **kwargs):
        raise RuntimeError("transcript is read-only")

    # _post_indexing_notice imports post_notice inside the function body,
    # so the binding to replace is the one in its defining module.
    from consensus.app_discussion_flow import helpers as flow_helpers
    monkeypatch.setattr(flow_helpers, "post_notice", boom)

    context = ToolContext(
        caller_entity_id=0, discussion_id=notice_app.discussion.id,
    )
    result = await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "q"},
        context, tmp_db, FakeEmbedClient(), notice_app,
    )
    # The tool call still reports the indexing failure it was called about.
    assert result.is_error
    assert "embedder is down" in result.content
