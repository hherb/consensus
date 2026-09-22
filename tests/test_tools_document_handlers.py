"""Characterization tests for the document RAG tool handlers.

Covers the eight ``doc_*`` tool handlers and the provider factory, pinning
their current behaviour ahead of the package split for issue #61. The
database is a real :class:`~consensus.database.Database`; only the embedding
client and the interpretation LLM are faked.
"""

import json

import pytest

from consensus.tools_document import (
    chunking, constants, embedding, handlers, handlers_rag, parsing,
)
from consensus.tools_document.provider import create_document_provider
from consensus.tools import ToolContext

from .document_helpers import (
    FakeApp, FakeEmbedClient, embed_all, patch_where_defined,
)


MARKDOWN = (
    "# Introduction\n\n"
    "The opening paragraph explains the purpose.\n\n"
    "## Methods\n\n"
    "We measured the thing carefully.\n\n"
    "## Results\n\n"
    "The thing was measured."
)


@pytest.fixture
def discussion_id(tmp_db, sample_ai_entity):
    """A discussion documents can be attached to."""
    return tmp_db.create_discussion("Topic", sample_ai_entity)


@pytest.fixture
def ctx(sample_ai_entity, discussion_id):
    """Tool context for the sample AI entity in the sample discussion."""
    return ToolContext(caller_entity_id=sample_ai_entity, discussion_id=discussion_id)


@pytest.fixture
def doc_id(tmp_db, discussion_id):
    """A stored, chunked document attached to the discussion."""
    sections = parsing.extract_sections(MARKDOWN)
    new_id = tmp_db.add_document(
        filename="paper.md", title="Paper", summary="A short summary.",
        mime_type="text/markdown", source_type="text", source_url=None,
        markdown=MARKDOWN, char_count=len(MARKDOWN),
        sections_json=json.dumps(sections),
    )
    tmp_db.add_discussion_document(discussion_id, new_id)
    for chunk in chunking.chunk_document(MARKDOWN, chunk_size=60, overlap=0):
        tmp_db.add_document_chunk(
            new_id, chunk["chunk_index"], chunk["content"],
            chunk["from_char"], chunk["to_char"], chunk.get("section_header"),
        )
    return new_id


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the interpretation LLM and record every call it receives.

    ``app`` is recorded alongside the prompts so a test can assert which
    object the provider factory actually forwarded to the handler.
    """
    calls = []

    async def _fake(app, context, system_prompt, user_prompt):
        calls.append({"app": app, "system": system_prompt, "user": user_prompt})
        return f"answer {len(calls)}"

    patch_where_defined(
        monkeypatch, handlers_rag._doc_ask_handler, "_call_interpretation_llm", _fake,
    )
    return calls


# ---------------------------------------------------------------------------
# doc_add
# ---------------------------------------------------------------------------

class TestDocAddHandler:
    @pytest.mark.asyncio
    async def test_requires_url_or_text(self, tmp_db, ctx):
        result = await handlers._doc_add_handler({}, ctx, tmp_db, None, None)
        assert result.is_error
        assert "url" in result.content and "text" in result.content

    @pytest.mark.asyncio
    async def test_blank_url_and_text_are_treated_as_absent(self, tmp_db, ctx):
        result = await handlers._doc_add_handler(
            {"url": "  ", "text": " "}, ctx, tmp_db, None, None,
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_inline_text_is_ingested_and_attached(self, tmp_db, ctx, discussion_id):
        result = await handlers._doc_add_handler(
            {"text": "# Note\n\nSome content."}, ctx, tmp_db, None, None,
        )
        assert not result.is_error
        assert result.metadata["title"] == "Note"
        stored = tmp_db.get_document(result.metadata["document_id"])
        assert stored["source_type"] == "text"
        assert stored["filename"] == "document.txt"
        assert [d["id"] for d in tmp_db.get_discussion_documents(discussion_id)] == [
            result.metadata["document_id"]
        ]

    @pytest.mark.asyncio
    async def test_explicit_title_and_filename_are_honoured(self, tmp_db, ctx):
        result = await handlers._doc_add_handler(
            {"text": "body", "title": "Chosen", "filename": "chosen.txt"},
            ctx, tmp_db, None, None,
        )
        stored = tmp_db.get_document(result.metadata["document_id"])
        assert stored["title"] == "Chosen"
        assert stored["filename"] == "chosen.txt"

    @pytest.mark.asyncio
    async def test_url_is_fetched_and_recorded_as_the_source(
        self, tmp_db, ctx, monkeypatch,
    ):
        async def fake_fetch(url):
            return b"# Fetched\n\nbody", "page.html", "text/html"

        patch_where_defined(
            monkeypatch, handlers._doc_add_handler, "fetch_url_content", fake_fetch,
        )
        result = await handlers._doc_add_handler(
            {"url": "http://x.test/page.html"}, ctx, tmp_db, None, None,
        )
        stored = tmp_db.get_document(result.metadata["document_id"])
        assert stored["source_type"] == "url"
        assert stored["source_url"] == "http://x.test/page.html"

    @pytest.mark.asyncio
    async def test_fetch_failure_is_surfaced_as_a_tool_error(
        self, tmp_db, ctx, monkeypatch,
    ):
        async def boom(url):
            raise RuntimeError("404 not found")

        patch_where_defined(
            monkeypatch, handlers._doc_add_handler, "fetch_url_content", boom,
        )
        result = await handlers._doc_add_handler(
            {"url": "http://x.test/gone"}, ctx, tmp_db, None, None,
        )
        assert result.is_error
        assert "404 not found" in result.content

    @pytest.mark.asyncio
    async def test_empty_document_is_surfaced_as_a_tool_error(
        self, tmp_db, ctx, monkeypatch,
    ):
        """Inline text is stripped first, so only a fetched body can be blank."""
        async def fake_fetch(url):
            return b"   \n  ", "blank.txt", "text/plain"

        patch_where_defined(
            monkeypatch, handlers._doc_add_handler, "fetch_url_content", fake_fetch,
        )
        result = await handlers._doc_add_handler(
            {"url": "http://x.test/blank"}, ctx, tmp_db, None, None,
        )
        assert result.is_error
        assert "empty" in result.content.lower()
        assert tmp_db.get_all_documents() == []

    @pytest.mark.asyncio
    async def test_content_is_json_matching_the_metadata(self, tmp_db, ctx):
        result = await handlers._doc_add_handler({"text": "body"}, ctx, tmp_db, None, None)
        assert json.loads(result.content) == result.metadata


# ---------------------------------------------------------------------------
# doc_list
# ---------------------------------------------------------------------------

class TestDocListHandler:
    @pytest.mark.asyncio
    async def test_empty_discussion_says_so(self, tmp_db, ctx):
        result = await handlers._doc_list_handler({}, ctx, tmp_db, None, None)
        assert "No documents attached" in result.content

    @pytest.mark.asyncio
    async def test_lists_documents_of_the_current_discussion(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_list_handler({}, ctx, tmp_db, None, None)
        assert f"[ID {doc_id}] Paper" in result.content
        assert result.metadata == {"count": 1}

    @pytest.mark.asyncio
    async def test_discussion_listing_excludes_unattached_documents(
        self, tmp_db, ctx, doc_id,
    ):
        tmp_db.add_document(
            filename="other.txt", title="Other", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="x", char_count=1,
            sections_json="[]",
        )
        result = await handlers._doc_list_handler({}, ctx, tmp_db, None, None)
        assert "Other" not in result.content

    @pytest.mark.asyncio
    async def test_full_library_lists_every_document(self, tmp_db, ctx, doc_id):
        tmp_db.add_document(
            filename="other.txt", title="Other", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="x", char_count=1,
            sections_json="[]",
        )
        result = await handlers._doc_list_handler(
            {"full_library": True}, ctx, tmp_db, None, None,
        )
        assert "Paper" in result.content and "Other" in result.content
        assert result.metadata == {"count": 2}

    @pytest.mark.asyncio
    async def test_empty_library_says_so(self, tmp_db, ctx):
        result = await handlers._doc_list_handler(
            {"full_library": True}, ctx, tmp_db, None, None,
        )
        assert "No documents in the library." in result.content

    @pytest.mark.asyncio
    async def test_long_summaries_are_truncated_with_an_ellipsis(self, tmp_db, ctx):
        tmp_db.add_discussion_document(
            ctx.discussion_id,
            tmp_db.add_document(
                filename="long.txt", title="Long", summary="s" * 300,
                mime_type="text/plain", source_type="text", source_url=None,
                markdown="x", char_count=1, sections_json="[]",
            ),
        )
        result = await handlers._doc_list_handler({}, ctx, tmp_db, None, None)
        assert "s" * 150 + "..." in result.content
        assert "s" * 151 not in result.content

    @pytest.mark.asyncio
    async def test_empty_summary_does_not_break_rendering(self, tmp_db, ctx):
        tmp_db.add_discussion_document(
            ctx.discussion_id,
            tmp_db.add_document(
                filename="n.txt", title="NoSummary", summary="",
                mime_type="text/plain", source_type="text", source_url=None,
                markdown="x", char_count=1, sections_json="[]",
            ),
        )
        result = await handlers._doc_list_handler({}, ctx, tmp_db, None, None)
        assert "NoSummary" in result.content

    @pytest.mark.asyncio
    async def test_library_search_ranks_documents_by_similarity(
        self, tmp_db, ctx, doc_id,
    ):
        embed_all(tmp_db, doc_id, (1.0, 0.0))
        result = await handlers._doc_list_handler(
            {"full_library": True, "query": "measurement"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
        )
        assert "Library search for 'measurement'" in result.content
        assert f"[ID {doc_id}] Paper" in result.content
        assert "score: 1.00" in result.content

    @pytest.mark.asyncio
    async def test_library_search_without_embeddings_says_the_library_is_empty(
        self, tmp_db, ctx, doc_id,
    ):
        result = await handlers._doc_list_handler(
            {"full_library": True, "query": "anything"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
        )
        assert "No documents in the library yet." in result.content

    @pytest.mark.asyncio
    async def test_library_search_below_threshold_reports_no_match(
        self, tmp_db, ctx, doc_id,
    ):
        embed_all(tmp_db, doc_id, (0.0, 1.0))
        result = await handlers._doc_list_handler(
            {"full_library": True, "query": "orthogonal"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
        )
        assert "No documents match 'orthogonal'." in result.content

    @pytest.mark.asyncio
    async def test_embedding_outage_is_an_error_not_an_empty_result(
        self, tmp_db, ctx, doc_id,
    ):
        result = await handlers._doc_list_handler(
            {"full_library": True, "query": "q"}, ctx, tmp_db,
            FakeEmbedClient(error=RuntimeError("ollama down")), None,
        )
        assert result.is_error
        assert "ollama down" in result.content


# ---------------------------------------------------------------------------
# doc_get_length / doc_get_text
# ---------------------------------------------------------------------------

class TestDocGetLengthHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers._doc_get_length_handler({}, ctx, tmp_db, None, None)
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers._doc_get_length_handler(
            {"document_id": 4242}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "not found" in result.content

    @pytest.mark.asyncio
    async def test_returns_the_stored_character_count(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_length_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, None,
        )
        assert json.loads(result.content)["char_count"] == len(MARKDOWN)
        assert result.metadata == {"char_count": len(MARKDOWN)}

    @pytest.mark.asyncio
    async def test_string_document_id_is_coerced(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_length_handler(
            {"document_id": str(doc_id)}, ctx, tmp_db, None, None,
        )
        assert not result.is_error


class TestDocGetTextHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers._doc_get_text_handler({}, ctx, tmp_db, None, None)
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers._doc_get_text_handler(
            {"document_id": 4242}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "not found" in result.content

    @pytest.mark.asyncio
    async def test_default_range_returns_the_whole_document(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_text_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, None,
        )
        assert result.content == MARKDOWN
        assert result.metadata["to_char"] == len(MARKDOWN)

    @pytest.mark.asyncio
    async def test_explicit_range_slices_the_markdown(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_text_handler(
            {"document_id": doc_id, "from_char": 2, "to_char": 14},
            ctx, tmp_db, None, None,
        )
        assert result.content == MARKDOWN[2:14]
        assert result.metadata["length"] == 12

    @pytest.mark.asyncio
    async def test_to_char_minus_one_means_end_of_document(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_text_handler(
            {"document_id": doc_id, "from_char": 10, "to_char": -1},
            ctx, tmp_db, None, None,
        )
        assert result.content == MARKDOWN[10:]


# ---------------------------------------------------------------------------
# doc_get_sections / doc_get_chapter
# ---------------------------------------------------------------------------

class TestDocGetSectionsHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers._doc_get_sections_handler({}, ctx, tmp_db, None, None)
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers._doc_get_sections_handler(
            {"document_id": 4242}, ctx, tmp_db, None, None,
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_lists_headers_with_offsets_and_indentation(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_sections_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, None,
        )
        assert "Sections in 'Paper' (3 total):" in result.content
        assert "# Introduction (chars 0-" in result.content
        assert "  ## Methods (chars" in result.content
        assert [s["header"] for s in result.metadata["sections"]] == [
            "Introduction", "Methods", "Results",
        ]

    @pytest.mark.asyncio
    async def test_document_without_sections_says_so(self, tmp_db, ctx):
        plain_id = tmp_db.add_document(
            filename="p.txt", title="Plain", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="no headers",
            char_count=10, sections_json="[]",
        )
        result = await handlers._doc_get_sections_handler(
            {"document_id": plain_id}, ctx, tmp_db, None, None,
        )
        assert "No sections found" in result.content
        assert not result.is_error


class TestDocGetChapterHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers._doc_get_chapter_handler(
            {"header": "Methods"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_header_is_required(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "header is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": 4242, "header": "Methods"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "not found" in result.content

    @pytest.mark.asyncio
    async def test_exact_header_returns_the_section_text(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id, "header": "Methods"}, ctx, tmp_db, None, None,
        )
        assert result.content.startswith("## Methods")
        assert "measured the thing carefully" in result.content
        assert "## Results" not in result.content
        assert result.metadata["header"] == "Methods"

    @pytest.mark.asyncio
    async def test_match_is_case_insensitive(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id, "header": "mEtHoDs"}, ctx, tmp_db, None, None,
        )
        assert result.metadata["header"] == "Methods"

    @pytest.mark.asyncio
    async def test_substring_match_is_accepted(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id, "header": "Intro"}, ctx, tmp_db, None, None,
        )
        assert result.metadata["header"] == "Introduction"

    @pytest.mark.asyncio
    async def test_unmatched_header_lists_the_available_ones(self, tmp_db, ctx, doc_id):
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id, "header": "Appendix"}, ctx, tmp_db, None, None,
        )
        assert result.is_error
        assert "Introduction, Methods, Results" in result.content

    @pytest.mark.asyncio
    async def test_text_vanishing_between_lookups_is_an_error(
        self, tmp_db, ctx, doc_id, monkeypatch,
    ):
        """The metadata row and the markdown are read separately."""
        monkeypatch.setattr(tmp_db, "get_document_markdown", lambda _id: None)
        result = await handlers._doc_get_chapter_handler(
            {"document_id": doc_id, "header": "Methods"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "Could not read document text" in result.content

    @pytest.mark.asyncio
    async def test_document_without_sections_is_an_error(self, tmp_db, ctx):
        plain_id = tmp_db.add_document(
            filename="p.txt", title="Plain", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="no headers",
            char_count=10, sections_json="[]",
        )
        result = await handlers._doc_get_chapter_handler(
            {"document_id": plain_id, "header": "Any"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "No sections found" in result.content


# ---------------------------------------------------------------------------
# doc_ask
# ---------------------------------------------------------------------------

class TestDocAskHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers_rag._doc_ask_handler(
            {"question": "why?"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_question_is_required(self, tmp_db, ctx, doc_id):
        result = await handlers_rag._doc_ask_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "question is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers_rag._doc_ask_handler(
            {"document_id": 4242, "question": "why?"}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "not found" in result.content

    @pytest.mark.asyncio
    async def test_unindexed_document_reports_progress_and_is_not_an_error(
        self, tmp_db, ctx, doc_id,
    ):
        result = await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "why?"}, ctx, tmp_db, None, None,
        )
        assert not result.is_error
        assert "still being indexed" in result.content
        total = len(tmp_db.get_document_chunks(doc_id))
        assert f"(0/{total} chunks embedded)" in result.content

    @pytest.mark.asyncio
    async def test_unindexed_document_rekicks_the_embedding_pass(
        self, tmp_db, ctx, doc_id, monkeypatch,
    ):
        spawned = []
        patch_where_defined(
            monkeypatch, handlers_rag._doc_ask_handler, "_spawn_embedding_pass",
            lambda doc_id, db, embed_client: spawned.append(doc_id),
        )
        try:
            await handlers_rag._doc_ask_handler(
                {"document_id": doc_id, "question": "why?"},
                ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
            )
            assert len(spawned) == 1
            assert doc_id in embedding._embedding_docs
        finally:
            embedding._embedding_docs.discard(doc_id)

    @pytest.mark.asyncio
    async def test_rekick_is_suppressed_while_a_pass_is_already_running(
        self, tmp_db, ctx, doc_id, monkeypatch,
    ):
        """Repeated doc_ask calls must not stack embedding passes.

        The AI retries doc_ask every turn while a document is indexing, so
        without the ``_embedding_docs`` guard each retry spawns another pass
        over the same chunks.
        """
        spawned = []
        patch_where_defined(
            monkeypatch, handlers_rag._doc_ask_handler, "_spawn_embedding_pass",
            lambda doc_id, db, embed_client: spawned.append(doc_id),
        )
        embedding._embedding_docs.add(doc_id)
        try:
            result = await handlers_rag._doc_ask_handler(
                {"document_id": doc_id, "question": "why?"},
                ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
            )
            assert spawned == []
            assert "still being indexed" in result.content
        finally:
            embedding._embedding_docs.discard(doc_id)

    @pytest.mark.asyncio
    async def test_embedding_outage_is_an_error(self, tmp_db, ctx, doc_id):
        embed_all(tmp_db, doc_id)
        result = await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "why?"}, ctx, tmp_db,
            FakeEmbedClient(error=RuntimeError("ollama down")), None,
        )
        assert result.is_error and "ollama down" in result.content

    @pytest.mark.asyncio
    async def test_answer_and_passages_are_returned(
        self, tmp_db, ctx, doc_id, fake_llm,
    ):
        embed_all(tmp_db, doc_id)
        result = await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "What was measured?"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )
        payload = json.loads(result.content)
        assert payload["answer"] == "answer 1"
        assert payload["relevant_passages"]
        assert all(
            {"text", "from_char", "to_char"} == set(p)
            for p in payload["relevant_passages"]
        )
        assert result.metadata == payload

    @pytest.mark.asyncio
    async def test_at_most_top_k_passages_are_used(self, tmp_db, ctx, fake_llm):
        many_id = tmp_db.add_document(
            filename="many.txt", title="Many", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="x" * 100,
            char_count=100, sections_json="[]",
        )
        for index in range(constants.RAG_TOP_K + 3):
            tmp_db.add_document_chunk(
                many_id, index, f"passage {index}", index * 10, index * 10 + 9, None,
            )
        embed_all(tmp_db, many_id)
        result = await handlers_rag._doc_ask_handler(
            {"document_id": many_id, "question": "q"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )
        assert len(result.metadata["relevant_passages"]) == constants.RAG_TOP_K

    @pytest.mark.asyncio
    async def test_prompt_carries_the_title_passages_and_question(
        self, tmp_db, ctx, doc_id, fake_llm,
    ):
        embed_all(tmp_db, doc_id)
        await handlers_rag._doc_ask_handler(
            {"document_id": doc_id, "question": "What was measured?"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )
        prompt = fake_llm[0]["user"]
        assert "DOCUMENT: Paper" in prompt
        assert "[Passage 1] (chars" in prompt
        assert "QUESTION: What was measured?" in prompt
        assert "ONLY on the" in fake_llm[0]["system"]

    @pytest.mark.asyncio
    async def test_passage_text_is_capped_in_the_returned_payload(
        self, tmp_db, ctx, discussion_id, fake_llm,
    ):
        long_markdown = "w" * 4000
        long_id = tmp_db.add_document(
            filename="long.txt", title="Long", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown=long_markdown,
            char_count=len(long_markdown), sections_json="[]",
        )
        tmp_db.add_document_chunk(long_id, 0, long_markdown, 0, 4000, None)
        embed_all(tmp_db, long_id)
        result = await handlers_rag._doc_ask_handler(
            {"document_id": long_id, "question": "q"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), FakeApp(tmp_db),
        )
        assert len(result.metadata["relevant_passages"][0]["text"]) == 500

    @pytest.mark.asyncio
    async def test_document_with_no_embedded_chunks_at_all_says_so(
        self, tmp_db, ctx, discussion_id,
    ):
        """``count_unembedded_chunks`` is 0 when there are no chunks at all."""
        bare_id = tmp_db.add_document(
            filename="bare.txt", title="Bare", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown="x", char_count=1,
            sections_json="[]",
        )
        result = await handlers_rag._doc_ask_handler(
            {"document_id": bare_id, "question": "q"},
            ctx, tmp_db, FakeEmbedClient([1.0, 0.0]), None,
        )
        assert "No embedded chunks found" in result.content


# ---------------------------------------------------------------------------
# doc_summary
# ---------------------------------------------------------------------------

class TestDocSummaryHandler:
    @pytest.mark.asyncio
    async def test_document_id_is_required(self, tmp_db, ctx):
        result = await handlers_rag._doc_summary_handler({}, ctx, tmp_db, None, None)
        assert result.is_error and "document_id is required" in result.content

    @pytest.mark.asyncio
    async def test_unknown_document_is_an_error(self, tmp_db, ctx):
        result = await handlers_rag._doc_summary_handler(
            {"document_id": 4242}, ctx, tmp_db, None, None,
        )
        assert result.is_error and "not found" in result.content

    @pytest.mark.asyncio
    async def test_empty_range_is_reported_without_calling_the_llm(
        self, tmp_db, ctx, doc_id, fake_llm,
    ):
        blank_start = MARKDOWN.index("\n\n")
        result = await handlers_rag._doc_summary_handler(
            {"document_id": doc_id, "from_char": blank_start, "to_char": blank_start + 2},
            ctx, tmp_db, None, None,
        )
        assert "Selected range is empty." in result.content
        assert fake_llm == []

    @pytest.mark.asyncio
    async def test_short_document_is_summarized_in_one_call(
        self, tmp_db, ctx, doc_id, fake_llm,
    ):
        result = await handlers_rag._doc_summary_handler(
            {"document_id": doc_id}, ctx, tmp_db, None, FakeApp(tmp_db),
        )
        assert json.loads(result.content) == {"summary": "answer 1"}
        assert len(fake_llm) == 1
        assert fake_llm[0]["user"] == MARKDOWN

    @pytest.mark.asyncio
    async def test_metadata_records_the_summarized_range(
        self, tmp_db, ctx, doc_id, fake_llm,
    ):
        result = await handlers_rag._doc_summary_handler(
            {"document_id": doc_id, "from_char": 0, "to_char": 30},
            ctx, tmp_db, None, FakeApp(tmp_db),
        )
        assert result.metadata["from_char"] == 0
        assert result.metadata["to_char"] == 30

    @pytest.mark.asyncio
    async def test_long_document_uses_map_reduce(self, tmp_db, ctx, fake_llm):
        long_markdown = "w" * (constants.SUMMARY_CHUNK_LIMIT * 2 + 10)
        long_id = tmp_db.add_document(
            filename="long.txt", title="Long", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown=long_markdown,
            char_count=len(long_markdown), sections_json="[]",
        )
        result = await handlers_rag._doc_summary_handler(
            {"document_id": long_id}, ctx, tmp_db, None, FakeApp(tmp_db),
        )
        # Three map calls over the excerpt, then one reduce call.
        assert len(fake_llm) == 4
        assert json.loads(result.content) == {"summary": "answer 4"}
        assert "Section 1:\nanswer 1" in fake_llm[-1]["user"]
        assert "Section 3:\nanswer 3" in fake_llm[-1]["user"]

    @pytest.mark.asyncio
    async def test_map_calls_respect_the_chunk_limit(self, tmp_db, ctx, fake_llm):
        long_markdown = "w" * (constants.SUMMARY_CHUNK_LIMIT + 1)
        long_id = tmp_db.add_document(
            filename="long.txt", title="Long", summary="", mime_type="text/plain",
            source_type="text", source_url=None, markdown=long_markdown,
            char_count=len(long_markdown), sections_json="[]",
        )
        await handlers_rag._doc_summary_handler(
            {"document_id": long_id}, ctx, tmp_db, None, FakeApp(tmp_db),
        )
        map_calls = fake_llm[:-1]
        assert [len(c["user"]) for c in map_calls] == [constants.SUMMARY_CHUNK_LIMIT, 1]


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

class TestCreateDocumentProvider:
    @pytest.mark.asyncio
    async def test_registers_every_document_tool(self, tmp_db):
        provider = create_document_provider(tmp_db)
        names = {t.name for t in await provider.list_tools()}
        assert names == {
            "doc_add", "doc_list", "doc_get_length", "doc_get_text",
            "doc_get_sections", "doc_get_chapter", "doc_ask", "doc_summary",
        }

    @pytest.mark.asyncio
    async def test_provider_is_named_documents(self, tmp_db):
        assert create_document_provider(tmp_db).name == "documents"

    @pytest.mark.asyncio
    async def test_every_tool_declares_a_description_and_schema(self, tmp_db):
        provider = create_document_provider(tmp_db)
        for tool in await provider.list_tools():
            assert tool.description.strip()
            assert tool.parameters["type"] == "object"

    @pytest.mark.asyncio
    async def test_execute_routes_through_to_the_handler(self, tmp_db, ctx, doc_id):
        provider = create_document_provider(tmp_db)
        result = await provider.execute(
            "doc_get_length", {"document_id": doc_id}, ctx,
        )
        assert json.loads(result.content)["char_count"] == len(MARKDOWN)

    @pytest.mark.asyncio
    async def test_handlers_receive_the_embed_client_and_app_from_the_factory(
        self, tmp_db, ctx, doc_id, fake_llm, monkeypatch,
    ):
        """``_make_handler`` must forward every dependency, in the right order.

        ``doc_ask`` is the only tool that needs ``db``, ``embed_client`` and
        ``app`` at once, so it is the one call that pins the whole wiring.
        Dropping either of the last two arguments leaves other tests green.
        """
        embed_all(tmp_db, doc_id, vector=(1.0, 0.0))

        class OneClient:
            """Embedding client shared by the factory and this test."""

            def __init__(self, db) -> None:
                self.calls = []

            async def embed(self, text):
                self.calls.append(text)
                return [1.0, 0.0]

        monkeypatch.setattr(
            "consensus.tools_memory.EmbeddingClient", OneClient,
        )
        app = FakeApp(tmp_db)
        # app.py calls this by keyword: create_document_provider(self.db, app=self)
        provider = create_document_provider(tmp_db, app=app)

        result = await provider.execute(
            "doc_ask", {"document_id": doc_id, "question": "why?"}, ctx,
        )

        assert not result.is_error, result.content
        # The embed_client reached the handler: the question was embedded.
        assert json.loads(result.content)["answer"] == "answer 1"
        # The app reached the handler: the interpretation LLM saw it.
        assert fake_llm[0]["app"] is app
