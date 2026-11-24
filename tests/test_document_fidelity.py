"""A degraded extraction stays visible after ingestion (issue #81).

``parse_document`` marks a regex tag-strip fallback as ``degraded`` with a
note. That used to travel only as far as the upload toast; these tests pin
that it is persisted and reaches every reader: the db rows, ``doc_list``,
``doc_ask``, ``doc_summary``, ``doc_get_text``, ``doc_get_chapter`` and the
MCP ``read_document`` / ``search_documents``. Also pins migration 018, which
flags the pre-#78 ``'(Empty PDF)'`` rows the same way (issue #80, option 1).
"""

import sys
import types

import pytest

from consensus.tools import ToolContext
from consensus.tools_document import chunking, handlers, handlers_rag, ingestion
from tests.document_helpers import (
    FakeApp, FakeEmbedClient, embed_all, patch_where_defined,
)
from tests.mcp_server_helpers import NEAR, call, server  # noqa: F401

NOTE = "Text was recovered by stripping HTML tags; it may contain navigation."


def _add(db, *, fidelity="full", notes=(), markdown="# Doc\n\nBody about widgets.",
         sections_json="[]"):
    """Store a document directly, the way ``ingest_document`` does."""
    return db.add_document(
        filename="d.md", title="Doc", summary="", mime_type="text/markdown",
        source_type="upload", source_url=None, markdown=markdown,
        char_count=len(markdown), sections_json=sections_json,
        fidelity=fidelity, parse_notes=list(notes),
    )


def test_rows_without_fidelity_read_back_as_full(tmp_db):
    """A row written before 017 reads as full fidelity with no notes."""
    tmp_db.conn.execute(
        "INSERT INTO documents (filename, title, summary, mime_type, "
        "source_type, source_url, markdown, char_count, sections_json, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a.md", "A", "", "text/markdown", "upload", None, "# A", 3, "[]", 0.0),
    )
    tmp_db.conn.commit()
    doc = tmp_db.get_all_documents()[0]
    assert (doc["fidelity"], doc["parse_notes"]) == ("full", [])


def test_degraded_fidelity_round_trips_through_every_reader(tmp_db):
    """add_document's fidelity and notes come back from all three readers."""
    doc_id = _add(tmp_db, fidelity="degraded", notes=[NOTE])
    disc_id = tmp_db.create_discussion("topic", 0)
    tmp_db.add_discussion_document(disc_id, doc_id)

    readers = (
        tmp_db.get_document(doc_id),
        tmp_db.get_all_documents()[0],
        tmp_db.get_discussion_documents(disc_id)[0],
    )
    for doc in readers:
        assert doc["fidelity"] == "degraded"
        assert doc["parse_notes"] == [NOTE]


def test_add_document_rejects_an_unknown_fidelity(tmp_db):
    """The column's domain is enforced on the one INSERT path."""
    with pytest.raises(ValueError, match="fidelity"):
        _add(tmp_db, fidelity="partial")


def test_add_document_rejects_degraded_without_a_reason(tmp_db):
    """A degraded row with no note would warn without saying why."""
    with pytest.raises(ValueError, match="note"):
        _add(tmp_db, fidelity="degraded")


@pytest.mark.asyncio
async def test_ingest_persists_a_degraded_extraction(tmp_db, monkeypatch):
    """The regex fallback's fidelity and notes reach the database."""
    fake = types.ModuleType("trafilatura")
    fake.extract = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "trafilatura", fake)

    result = await ingestion.ingest_document(
        app=None, db=tmp_db, embed_client=None,
        content_bytes=b"<html><body><p>Hello</p></body></html>",
        filename="p.html", mime_type="text/html",
    )
    doc = tmp_db.get_document(result["document_id"])
    assert doc["fidelity"] == "degraded"
    assert doc["parse_notes"] == result["notes"]
    assert doc["parse_notes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["discussion", "library", "search"])
async def test_doc_list_warns_about_a_degraded_document(
    tmp_db, sample_ai_entity, mode,
):
    """Every doc_list mode names the degraded document and why.

    The search mode builds its rows from selected fields, which is how it
    once dropped ``summary_status``; it is checked here for the same reason.
    """
    bad = _add(tmp_db, fidelity="degraded", notes=[NOTE])
    good = _add(tmp_db, markdown="# Fine\n\nBody about widgets too.")
    disc_id = tmp_db.create_discussion("topic", 0)
    for doc_id in (bad, good):
        tmp_db.add_discussion_document(disc_id, doc_id)
        for chunk in chunking.chunk_document(
            tmp_db.get_document_markdown(doc_id), chunk_size=60, overlap=0,
        ):
            tmp_db.add_document_chunk(
                doc_id, chunk["chunk_index"], chunk["content"],
                chunk["from_char"], chunk["to_char"], None,
            )
        embed_all(tmp_db, doc_id, (1.0, 0.0))

    args = {
        "discussion": {},
        "library": {"full_library": True},
        "search": {"full_library": True, "query": "widgets"},
    }[mode]
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=disc_id)
    result = await handlers._doc_list_handler(
        args, context, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
    )
    assert result.content.count("Degraded extraction") == 1
    assert NOTE in result.content


@pytest.mark.asyncio
async def test_doc_ask_carries_the_extraction_warning(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """An answer drawn from degraded text says so; a full one carries no key."""
    bad = _add(tmp_db, fidelity="degraded", notes=[NOTE])
    good = _add(tmp_db)
    for doc_id in (bad, good):
        tmp_db.add_document_chunk(doc_id, 0, "Body about widgets.", 0, 19, None)
        embed_all(tmp_db, doc_id, (1.0, 0.0))

    async def answer(*args, **kwargs):
        return "Widgets."

    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler,
        "_call_interpretation_llm", answer,
    )
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)

    async def run(doc_id):
        return await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "what?"},
            context, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )

    degraded = await run(bad)
    assert not degraded.is_error
    assert NOTE in degraded.metadata["extraction_warning"]
    assert "extraction_warning" not in (await run(good)).metadata


@pytest.mark.asyncio
async def test_doc_summary_carries_the_extraction_warning(
    tmp_db, sample_ai_entity, monkeypatch,
):
    """A summary of degraded text says so; a full one carries no key."""
    bad = _add(tmp_db, fidelity="degraded", notes=[NOTE])
    good = _add(tmp_db)

    async def summarise(*args, **kwargs):
        return "A summary."

    patch_where_defined(
        monkeypatch, handlers_rag._doc_summary_handler,
        "_call_interpretation_llm", summarise,
    )
    context = ToolContext(caller_entity_id=sample_ai_entity, discussion_id=0)

    async def run(doc_id):
        return await handlers_rag._doc_summary_handler(
            {"document_id": doc_id}, context, tmp_db, None, FakeApp(tmp_db),
        )

    degraded = await run(bad)
    assert NOTE in degraded.content
    assert NOTE in degraded.metadata["extraction_warning"]
    assert "extraction_warning" not in (await run(good)).metadata


def test_migration_flags_pre_78_empty_pdf_rows(tmp_db):
    """018 marks the '(Empty PDF)' rows degraded and leaves their text alone.

    Exercised through the real migrator, not by running the SQL by hand.
    """
    from consensus import migrator

    for filename, markdown in (("scan.pdf", "(Empty PDF)"), ("ok.md", "# Ok")):
        tmp_db.conn.execute(
            "INSERT INTO documents (filename, title, summary, mime_type, "
            "source_type, source_url, markdown, char_count, sections_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (filename, filename, "", "application/pdf", "upload", None,
             markdown, len(markdown), "[]", 0.0),
        )
    tmp_db.conn.execute("DELETE FROM migrations WHERE version=018")
    tmp_db.conn.commit()
    migrator._migrations_done.discard(tmp_db.db_path)
    migrator.run_migrations(tmp_db.conn, tmp_db._lock, tmp_db.db_path)

    docs = {d["filename"]: d for d in tmp_db.get_all_documents()}
    assert docs["scan.pdf"]["fidelity"] == "degraded"
    assert "OCR" in docs["scan.pdf"]["parse_notes"][0]
    assert tmp_db.get_document_markdown(docs["scan.pdf"]["id"]) == "(Empty PDF)"
    assert (docs["ok.md"]["fidelity"], docs["ok.md"]["parse_notes"]) == ("full", [])


# The raw-text readers: each returns stored text (or says it found none),
# so each must say when that text is a degraded extraction.
_SECTIONS = '[{"header": "Doc", "level": 1, "from_char": 0, "to_char": 26}]'


async def _read_doc_get_text(srv, doc_id):
    return (await handlers._doc_get_text_handler(
        {"document_id": doc_id}, ToolContext(0, 0), srv._db, None, None)).content


async def _read_doc_get_chapter(srv, doc_id):
    return (await handlers._doc_get_chapter_handler(
        {"document_id": doc_id, "header": "Doc"}, ToolContext(0, 0),
        srv._db, None, None)).content


async def _read_doc_ask_irrelevant(srv, doc_id):
    # An orthogonal question vector: nothing clears the relevance threshold.
    return (await handlers_rag._doc_ask_handler(
        {"document_id": doc_id, "question": "unrelated?"}, ToolContext(0, 0),
        srv._db, FakeEmbedClient([0.0, 1.0, 0.0]), FakeApp(srv._db))).content


async def _read_doc_summary_blank_range(srv, doc_id):
    return (await handlers_rag._doc_summary_handler(
        {"document_id": doc_id, "from_char": 5, "to_char": 7},
        ToolContext(0, 0), srv._db, None, FakeApp(srv._db))).content


async def _read_mcp_document(srv, doc_id):
    return (await call(srv, "read_document", {"document_id": doc_id}))[0]


async def _read_mcp_section(srv, doc_id):
    return (await call(srv, "read_document",
                       {"document_id": doc_id, "section_header": "Doc"}))[0]


async def _read_mcp_search(srv, doc_id):
    return (await call(srv, "search_documents", {"query": "widgets"}))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fidelity", ["degraded", "full"])
@pytest.mark.parametrize("read", [
    _read_doc_get_text, _read_doc_get_chapter, _read_doc_ask_irrelevant,
    _read_doc_summary_blank_range, _read_mcp_document, _read_mcp_section,
    _read_mcp_search,
], ids=lambda f: f.__name__.removeprefix("_read_"))
async def test_raw_text_readers_warn_about_a_degraded_document(
    server, read, fidelity,
):
    """Only a degraded document's text comes back with the warning."""
    notes = [NOTE] if fidelity == "degraded" else []
    doc_id = _add(server._db, fidelity=fidelity, notes=notes,
                  sections_json=_SECTIONS)
    server._db.add_document_chunk(doc_id, 0, "# Doc\n\nBody about widgets.", 0, 26)
    embed_all(server._db, doc_id, NEAR)

    content = await read(server, doc_id)
    assert ("degraded extraction" in content.lower()) == (fidelity == "degraded")


def test_upgrade_from_016_backfills_and_flags_existing_rows(tmp_path):
    """Rows stored before 017 come through 017 then 018 as a real upgrade does."""
    import shutil
    import sqlite3
    import threading

    from consensus import migrator

    real_dir = migrator._get_migrations_dir()
    old_dir = tmp_path / "upto016"
    old_dir.mkdir()
    for version, name, path in migrator._discover_migrations(real_dir):
        if version <= 16:
            shutil.copy(path, old_dir / name)

    db_path = str(tmp_path / "old.db")
    conn, lock = sqlite3.connect(db_path), threading.Lock()
    migrator.run_migrations(conn, lock, db_path, str(old_dir))
    for filename, markdown in (("scan.pdf", "(Empty PDF)"), ("ok.md", "# Ok")):
        conn.execute(
            "INSERT INTO documents (filename, title, summary, mime_type, "
            "source_type, source_url, markdown, char_count, sections_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (filename, filename, "", "text/plain", "upload", None,
             markdown, len(markdown), "[]", 0.0),
        )
    conn.commit()
    migrator._migrations_done.discard(db_path)
    migrator.run_migrations(conn, lock, db_path, real_dir)

    rows = dict(conn.execute(
        "SELECT filename, fidelity FROM documents").fetchall())
    conn.close()
    assert rows == {"scan.pdf": "degraded", "ok.md": "full"}
