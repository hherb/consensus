"""Characterization tests for the document RAG stateful pipeline.

Covers the background embedding pass, the interpretation-LLM helper and the
ingestion pipeline of ``consensus.tools_document``, pinning their behaviour
ahead of the package split for issue #61. The database is a real
:class:`~consensus.database.Database`; only the network edges are faked.
"""

import sqlite3

import pytest

from consensus.tools_document import constants, embedding, ingestion, llm
from consensus.tools import ToolContext

from .document_helpers import (
    FakeAIClient, FakeApp, FakeEmbedClient, patch_where_defined,
)


@pytest.fixture
def ctx():
    """A tool context for a caller entity in a discussion."""
    return ToolContext(caller_entity_id=1, discussion_id=2)


# ---------------------------------------------------------------------------
# Background embedding
# ---------------------------------------------------------------------------

@pytest.fixture
def doc_with_chunks(tmp_db):
    """Insert a document with three chunks and return (doc_id, chunk_ids)."""
    doc_id = tmp_db.add_document(
        filename="d.txt", title="Doc", summary="", mime_type="text/plain",
        source_type="text", source_url=None, markdown="body", char_count=4,
        sections_json="[]",
    )
    chunk_ids = [
        tmp_db.add_document_chunk(doc_id, i, f"chunk {i}", i * 10, i * 10 + 9, "Sec")
        for i in range(3)
    ]
    return doc_id, chunk_ids


class TestEmbedSingleChunk:
    @pytest.mark.asyncio
    async def test_success_stores_the_embedding(self, tmp_db, doc_with_chunks):
        doc_id, chunk_ids = doc_with_chunks
        chunk = tmp_db.get_document_chunks(doc_id)[0]
        client = FakeEmbedClient([0.1, 0.2])

        assert await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client) is True

        stored = tmp_db.get_chunks_with_embeddings(doc_id)
        assert [c["id"] for c in stored] == [chunk_ids[0]]
        assert embedding._unpack_embedding(stored[0]["embedding"]) == pytest.approx([0.1, 0.2])

    @pytest.mark.asyncio
    async def test_generic_failure_returns_false_and_stores_nothing(
        self, tmp_db, doc_with_chunks,
    ):
        doc_id, _ = doc_with_chunks
        chunk = tmp_db.get_document_chunks(doc_id)[0]
        client = FakeEmbedClient(error=RuntimeError("endpoint down"))

        assert await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client) is False
        assert tmp_db.get_chunks_with_embeddings(doc_id) == []

    @pytest.mark.asyncio
    async def test_oversized_chunk_is_replaced_by_embedded_sub_chunks(
        self, tmp_db, doc_with_chunks, monkeypatch,
    ):
        from consensus.tools_memory import EmbeddingContextLengthError

        doc_id, chunk_ids = doc_with_chunks
        long_text = "z" * 1200
        big_id = tmp_db.add_document_chunk(doc_id, 99, long_text, 0, 1200, "Sec")
        chunk = [c for c in tmp_db.get_document_chunks(doc_id) if c["id"] == big_id][0]

        client = FakeEmbedClient(
            [0.3], errors_by_text={long_text: EmbeddingContextLengthError("too long")},
        )

        assert await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client) is True

        remaining = tmp_db.get_document_chunks(doc_id)
        assert big_id not in [c["id"] for c in remaining]
        sub_chunks = [c for c in remaining if c["id"] not in chunk_ids]
        assert len(sub_chunks) == 3
        assert all(c["section_header"] == "Sec" for c in sub_chunks)
        embedded_ids = {c["id"] for c in tmp_db.get_chunks_with_embeddings(doc_id)}
        assert embedded_ids == {c["id"] for c in sub_chunks}

    @pytest.mark.asyncio
    async def test_sub_chunk_indices_continue_after_the_existing_maximum(
        self, tmp_db, doc_with_chunks,
    ):
        from consensus.tools_memory import EmbeddingContextLengthError

        doc_id, _ = doc_with_chunks
        long_text = "z" * 1200
        big_id = tmp_db.add_document_chunk(doc_id, 7, long_text, 0, 1200, None)
        chunk = [c for c in tmp_db.get_document_chunks(doc_id) if c["id"] == big_id][0]
        client = FakeEmbedClient(
            [0.3], errors_by_text={long_text: EmbeddingContextLengthError("x")},
        )

        await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client)

        indices = sorted(c["chunk_index"] for c in tmp_db.get_document_chunks(doc_id))
        assert indices == [0, 1, 2, 8, 9, 10]

    @pytest.mark.asyncio
    async def test_failing_sub_chunk_reports_failure_but_keeps_the_others(
        self, tmp_db, doc_with_chunks,
    ):
        from consensus.tools_memory import EmbeddingContextLengthError

        doc_id, _ = doc_with_chunks
        # Distinct characters throughout, so the per-text failure below hits
        # exactly one sub-chunk rather than every identical copy of it.
        long_text = "".join(chr(ord("a") + i % 26) for i in range(1200))
        big_id = tmp_db.add_document_chunk(doc_id, 99, long_text, 0, 1200, None)
        chunk = [c for c in tmp_db.get_document_chunks(doc_id) if c["id"] == big_id][0]
        first_sub = long_text[:constants.DEFAULT_CHUNK_SIZE]
        client = FakeEmbedClient([0.3], errors_by_text={
            long_text: EmbeddingContextLengthError("x"),
            first_sub: RuntimeError("sub failed"),
        })

        assert await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client) is False
        assert len(tmp_db.get_chunks_with_embeddings(doc_id)) == 2
        # The oversized parent is removed even when a sub-chunk fails.
        assert big_id not in [c["id"] for c in tmp_db.get_document_chunks(doc_id)]

    @pytest.mark.asyncio
    async def test_oversized_chunk_with_no_siblings_does_not_raise(self, tmp_db):
        """``max()`` over an empty chunk list must not abort the pass."""
        from consensus.tools_memory import EmbeddingContextLengthError

        doc_id = tmp_db.add_document(
            filename="d.txt", title="D", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="x", char_count=1,
            sections_json="[]",
        )
        chunk = {
            "id": 12345, "chunk_index": 0, "content": "z" * 1200,
            "from_char": 0, "to_char": 1200, "section_header": None,
        }
        client = FakeEmbedClient([0.3], errors_by_text={
            chunk["content"]: EmbeddingContextLengthError("x"),
        })

        assert await embedding._embed_single_chunk(chunk, doc_id, tmp_db, client) is True
        assert sorted(c["chunk_index"] for c in tmp_db.get_document_chunks(doc_id)) == [0, 1, 2]


class TestEmbedDocumentChunks:
    @pytest.mark.asyncio
    async def test_embeds_every_unembedded_chunk(self, tmp_db, doc_with_chunks):
        doc_id, chunk_ids = doc_with_chunks
        client = FakeEmbedClient([1.0])

        await embedding._embed_document_chunks(doc_id, tmp_db, client)

        assert tmp_db.count_unembedded_chunks(doc_id) == 0
        assert len(client.calls) == len(chunk_ids)

    @pytest.mark.asyncio
    async def test_already_embedded_chunks_are_skipped(self, tmp_db, doc_with_chunks):
        doc_id, chunk_ids = doc_with_chunks
        tmp_db.set_chunk_embedding(chunk_ids[0], embedding._pack_embedding([9.0]))
        client = FakeEmbedClient([1.0])

        await embedding._embed_document_chunks(doc_id, tmp_db, client)

        assert client.calls == ["chunk 1", "chunk 2"]

    @pytest.mark.asyncio
    async def test_releases_the_in_progress_marker_on_success(self, tmp_db, doc_with_chunks):
        doc_id, _ = doc_with_chunks
        embedding._embedding_docs.add(doc_id)
        try:
            await embedding._embed_document_chunks(doc_id, tmp_db, FakeEmbedClient([1.0]))
            assert doc_id not in embedding._embedding_docs
        finally:
            embedding._embedding_docs.discard(doc_id)

    @pytest.mark.asyncio
    async def test_releases_the_in_progress_marker_when_every_chunk_fails(
        self, tmp_db, doc_with_chunks,
    ):
        doc_id, _ = doc_with_chunks
        embedding._embedding_docs.add(doc_id)

        class Exploding:
            async def embed(self, _text):
                raise RuntimeError("boom")

        try:
            await embedding._embed_document_chunks(doc_id, tmp_db, Exploding())
            assert doc_id not in embedding._embedding_docs
        finally:
            embedding._embedding_docs.discard(doc_id)

    @pytest.mark.asyncio
    async def test_marker_is_released_when_the_pass_itself_raises(
        self, tmp_db, doc_with_chunks,
    ):
        """The ``finally`` exists for a raise *before* the per-chunk loop.

        A DB error in ``get_document_chunks`` would otherwise leave the
        document marked "indexing" forever, and the re-kick guard in
        ``_doc_ask_handler`` then refuses to ever start it again.
        """
        doc_id, _ = doc_with_chunks

        class ExplodingDb:
            def __getattr__(self, name):
                raise sqlite3.OperationalError("database is locked")

        embedding._embedding_docs.add(doc_id)
        try:
            with pytest.raises(sqlite3.OperationalError):
                await embedding._embed_document_chunks(
                    doc_id, ExplodingDb(), FakeEmbedClient([1.0]),
                )
            assert doc_id not in embedding._embedding_docs
        finally:
            embedding._embedding_docs.discard(doc_id)

    @pytest.mark.asyncio
    async def test_failures_are_logged_with_a_count(self, tmp_db, doc_with_chunks, caplog):
        doc_id, _ = doc_with_chunks
        client = FakeEmbedClient(error=RuntimeError("down"))

        with caplog.at_level("WARNING", logger="consensus.tools_document"):
            await embedding._embed_document_chunks(doc_id, tmp_db, client)

        assert any("3/3 chunks failed" in r.getMessage() for r in caplog.records)


class TestSpawnBackground:
    @pytest.mark.asyncio
    async def test_reference_is_held_while_running_and_released_when_done(self):
        """asyncio only weakly references tasks; an unretained one can vanish.

        ``embedding.py`` no longer has its own spawn helper (issue #78 task
        1): scheduling now goes through the shared ``consensus.background``
        module, so this test exercises that module directly.
        """
        import asyncio

        from consensus import background

        started = asyncio.Event()
        release = asyncio.Event()

        async def work():
            started.set()
            await release.wait()

        background.spawn_background(work(), "test reference is held")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        running = [t for t in background._background_tasks if not t.done()]
        assert running, "task was not retained while running"

        release.set()
        await asyncio.wait_for(asyncio.gather(*running), timeout=1.0)
        await asyncio.sleep(0)
        assert not any(t in background._background_tasks for t in running)


# ---------------------------------------------------------------------------
# Interpretation LLM helper
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_app(tmp_db, sample_ai_entity):
    """A fake app whose DB holds one AI entity."""
    return FakeApp(tmp_db), sample_ai_entity


class TestCallInterpretationLlm:
    @pytest.mark.asyncio
    async def test_unknown_entity_returns_an_explanatory_string(self, tmp_db):
        app = FakeApp(tmp_db)
        result = await llm._call_interpretation_llm(
            app, ToolContext(caller_entity_id=99999, discussion_id=1),
            "system", "user",
        )
        assert "could not resolve caller entity" in result

    @pytest.mark.asyncio
    async def test_returns_the_completion_content(self, monkeypatch, llm_app):
        app, entity_id = llm_app
        patch_where_defined(
            monkeypatch, llm._call_interpretation_llm, "AIClient", FakeAIClient,
        )
        result = await llm._call_interpretation_llm(
            app, ToolContext(caller_entity_id=entity_id, discussion_id=1),
            "sys prompt", "user prompt",
        )
        assert result == "canned answer"
        assert FakeAIClient.last_call["messages"] == [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "user prompt"},
        ]

    @pytest.mark.asyncio
    async def test_uses_the_caller_entity_model_and_resolved_key(
        self, monkeypatch, llm_app,
    ):
        app, entity_id = llm_app
        patch_where_defined(
            monkeypatch, llm._call_interpretation_llm, "AIClient", FakeAIClient,
        )
        await llm._call_interpretation_llm(
            app, ToolContext(caller_entity_id=entity_id, discussion_id=1), "s", "u",
        )
        assert FakeAIClient.last_call["model"] == "test-model"
        assert FakeAIClient.last_init["api_key"] == "resolved-key"
        assert FakeAIClient.last_init["timeout"] == constants.LLM_TIMEOUT
        assert FakeAIClient.last_call["temperature"] == constants.INTERPRETATION_TEMPERATURE
        assert app.resolved and app.resolved[0][1] == "TEST_API_KEY"

    @pytest.mark.asyncio
    async def test_client_is_closed_even_on_success(self, monkeypatch, llm_app):
        app, entity_id = llm_app
        patch_where_defined(
            monkeypatch, llm._call_interpretation_llm, "AIClient", FakeAIClient,
        )
        await llm._call_interpretation_llm(
            app, ToolContext(caller_entity_id=entity_id, discussion_id=1), "s", "u",
        )
        assert FakeAIClient.closed is True

    @pytest.mark.asyncio
    async def test_failure_is_reported_in_the_returned_text(self, monkeypatch, llm_app):
        app, entity_id = llm_app

        class FailingClient(FakeAIClient):
            async def complete(self, **kwargs):
                raise RuntimeError("provider exploded")

        patch_where_defined(
            monkeypatch, llm._call_interpretation_llm, "AIClient", FailingClient,
        )
        result = await llm._call_interpretation_llm(
            app, ToolContext(caller_entity_id=entity_id, discussion_id=1), "s", "u",
        )
        assert "LLM call failed" in result and "provider exploded" in result
        assert FakeAIClient.closed is True


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------

class TestIngestDocument:
    @pytest.mark.asyncio
    async def test_empty_document_reports_an_error(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"   ", filename="empty.txt", mime_type="text/plain",
        )
        assert result == {"error": "Document is empty after parsing."}
        assert tmp_db.get_all_documents() == []

    @pytest.mark.asyncio
    async def test_stores_document_sections_and_chunks(self, tmp_db):
        markdown = "# Heading\n\n" + "body text. " * 200
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=markdown.encode(), filename="d.md", mime_type="text/markdown",
        )
        doc_id = result["document_id"]
        assert tmp_db.get_document_markdown(doc_id) == markdown
        assert tmp_db.get_document(doc_id)["char_count"] == len(markdown)
        assert result["sections"] == 1
        assert result["chunks"] == len(tmp_db.get_document_chunks(doc_id))
        assert result["chunks"] > 1

    @pytest.mark.asyncio
    async def test_title_defaults_to_the_first_header(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"# Detected Title\n\nbody", filename="d.md",
            mime_type="text/markdown",
        )
        assert result["title"] == "Detected Title"

    @pytest.mark.asyncio
    async def test_title_defaults_to_the_filename_without_headers(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"no headers here", filename="notes.txt",
            mime_type="text/plain",
        )
        assert result["title"] == "notes.txt"

    @pytest.mark.asyncio
    async def test_explicit_title_wins_over_the_first_header(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"# Ignored\n\nbody", filename="d.md",
            mime_type="text/markdown", title="Chosen",
        )
        assert result["title"] == "Chosen"

    @pytest.mark.asyncio
    async def test_document_is_attached_to_the_discussion(self, tmp_db, sample_ai_entity):
        disc_id = tmp_db.create_discussion("Topic", sample_ai_entity)
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"body text", filename="d.txt", mime_type="text/plain",
            discussion_id=disc_id,
        )
        attached = tmp_db.get_discussion_documents(disc_id)
        assert [d["id"] for d in attached] == [result["document_id"]]

    @pytest.mark.asyncio
    async def test_summary_is_skipped_without_app_or_context(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None,
            content_bytes=b"body text", filename="d.txt", mime_type="text/plain",
            generate_summary=True,
        )
        assert result["summary"] == ""

    @pytest.mark.asyncio
    async def test_summary_uses_the_first_excerpt_of_the_document(
        self, tmp_db, monkeypatch, ctx,
    ):
        seen = {}

        async def fake_llm(app, context, system_prompt, user_prompt):
            seen["user_prompt"] = user_prompt
            return "a two sentence summary"

        patch_where_defined(
            monkeypatch, ingestion.ingest_document, "_call_interpretation_llm", fake_llm,
        )
        markdown = "q" * 5000
        result = await ingestion.ingest_document(
            app=FakeApp(tmp_db), db=tmp_db, embed_client=None,
            content_bytes=markdown.encode(), filename="d.txt",
            mime_type="text/plain", context=ctx,
        )
        assert result["summary"] == "a two sentence summary"
        assert len(seen["user_prompt"]) == 3000

    @pytest.mark.asyncio
    async def test_summary_failure_leaves_an_empty_summary(self, tmp_db, monkeypatch, ctx):
        async def boom(*_args, **_kwargs):
            raise RuntimeError("llm down")

        patch_where_defined(
            monkeypatch, ingestion.ingest_document, "_call_interpretation_llm", boom,
        )
        result = await ingestion.ingest_document(
            app=FakeApp(tmp_db), db=tmp_db, embed_client=None,
            content_bytes=b"body text", filename="d.txt", mime_type="text/plain",
            context=ctx,
        )
        assert result["summary"] == ""
        assert result["document_id"] > 0

    @pytest.mark.asyncio
    async def test_ingestion_spawns_one_embedding_pass_and_marks_the_document(
        self, tmp_db, monkeypatch,
    ):
        spawned = []
        patch_where_defined(
            monkeypatch, ingestion.ingest_document, "_spawn_embedding_pass",
            lambda doc_id, db, embed_client: spawned.append(doc_id),
        )
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=FakeEmbedClient(), content_bytes=b"body",
            filename="d.txt", mime_type="text/plain",
        )
        try:
            assert len(spawned) == 1
            assert result["document_id"] in embedding._embedding_docs
        finally:
            embedding._embedding_docs.discard(result["document_id"])

    @pytest.mark.asyncio
    async def test_no_second_pass_while_one_is_already_in_progress(
        self, tmp_db, monkeypatch,
    ):
        """The ``_embedding_docs`` marker is the duplicate-spawn guard.

        Without it, concurrent ingestion of the same document runs overlapping
        passes over the same chunks, racing ``add_document_chunk`` /
        ``delete_document_chunk`` for oversized chunks.
        """
        spawned = []
        patch_where_defined(
            monkeypatch, ingestion.ingest_document, "_spawn_embedding_pass",
            lambda doc_id, db, embed_client: spawned.append(doc_id),
        )
        # Pre-claim every id this ingestion could be assigned.
        claimed = set(range(1, 50))
        embedding._embedding_docs.update(claimed)
        try:
            await ingestion.ingest_document(
                app=None, db=tmp_db, embed_client=FakeEmbedClient(),
                content_bytes=b"body", filename="d.txt", mime_type="text/plain",
            )
            assert spawned == []
        finally:
            embedding._embedding_docs.difference_update(claimed)

    @pytest.mark.asyncio
    async def test_no_embedding_without_an_embed_client(self, tmp_db, monkeypatch):
        spawned = []
        patch_where_defined(
            monkeypatch, ingestion.ingest_document, "_spawn_embedding_pass",
            lambda doc_id, db, embed_client: spawned.append(doc_id),
        )
        await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None, content_bytes=b"body",
            filename="d.txt", mime_type="text/plain",
        )
        assert spawned == []

    @pytest.mark.asyncio
    async def test_source_url_and_type_are_recorded(self, tmp_db):
        result = await ingestion.ingest_document(
            app=None, db=tmp_db, embed_client=None, content_bytes=b"body",
            filename="page.html", mime_type="text/html",
            source_url="http://x.test/page.html", source_type="url",
        )
        stored = tmp_db.get_document(result["document_id"])
        assert stored["source_url"] == "http://x.test/page.html"
        assert stored["source_type"] == "url"
