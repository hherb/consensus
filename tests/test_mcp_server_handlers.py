"""Characterization tests for the MCP server's tool handlers and stdio loop.

Written before splitting ``consensus/mcp_server.py`` (issue #61) to cover what
``tests/test_mcp_server.py`` did not: the semantic-search handlers, document
sections, knowledge-graph neighbours, embedding failures, a full
``run_discussion`` against the fake LLM edge, and the stdin/stdout loop.

Every tool is driven through ``handle_request`` — the entry point production
uses — so the tests survive the handlers moving between modules. The only
fakes sit at network edges: the embedding client (set on the instance) and
``AIClient._post_with_retry`` (the ``llm`` fixture).
"""

import asyncio
import io
import json
import sys

import pytest

from consensus import mcp_server
from consensus.mcp_server import AGENT_ENTITY_NAME, ConsensusMCPServer
from consensus.tools_memory import _pack_embedding
from tests.document_helpers import FakeEmbedClient, patch_where_defined
from tests.eval_runner_helpers import (  # noqa: F401
    is_summary, is_synthesis, is_turn, llm,
)

#: Two orthogonal unit vectors: the query matches ``NEAR`` and not ``FAR``.
NEAR = [1.0, 0.0, 0.0]
FAR = [0.0, 1.0, 0.0]


@pytest.fixture
def server(tmp_path):
    """A server on a temporary database with a fake embedding client."""
    srv = ConsensusMCPServer(str(tmp_path / "test.db"))
    srv._embed_client = FakeEmbedClient(vector=NEAR)
    yield srv
    srv.close()


@pytest.fixture
def broken_embed(server):
    """The embedding service is down."""
    server._embed_client = FakeEmbedClient(error=ConnectionError("ollama down"))
    return server


async def call(server, name: str, arguments: dict) -> tuple[str, bool]:
    """Call a tool; return ``(text, is_error)``."""
    resp = await server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    result = resp["result"]
    return result["content"][0]["text"], bool(result.get("isError"))


def _ai_entities(db, count: int = 2) -> list[int]:
    prov = db.add_provider("P", "http://localhost:1/v1", "")
    return [db.add_entity(f"AI{i}", "ai", "#000", prov, "m", 0.5, 512, "")
            for i in range(count)]


# ---------------------------------------------------------------------------
# Read handlers
# ---------------------------------------------------------------------------

class TestReadDiscussion:
    async def test_messages_and_storyboard_can_be_left_out(self, server):
        db = server._db
        eid = _ai_entities(db, 1)[0]
        did = db.create_discussion("Topic", eid)
        db.add_message(did, eid, "hello", "participant")
        text, _ = await call(server, "read_discussion", {
            "discussion_id": did, "include_messages": False,
            "include_storyboard": False})
        data = json.loads(text)
        assert "messages" not in data
        assert "storyboard" not in data

    async def test_messages_included_by_default(self, server):
        db = server._db
        eid = _ai_entities(db, 1)[0]
        did = db.create_discussion("Topic", eid)
        db.add_message(did, eid, "hello", "participant")
        data = json.loads((await call(server, "read_discussion",
                                      {"discussion_id": did}))[0])
        assert [m["content"] for m in data["messages"]] == ["hello"]
        assert "storyboard" in data


def _add_doc(db, sections: str, markdown: str = "# A\n\nalpha") -> int:
    return db.add_document("d.md", "Doc", "sum", "text/markdown", "text",
                           None, markdown, len(markdown), sections)


class TestDocuments:
    async def test_list_documents(self, server):
        doc_id = _add_doc(server._db, "[]")
        rows = json.loads((await call(server, "list_documents", {}))[0])
        assert rows == [{"id": doc_id, "title": "Doc", "filename": "d.md",
                         "summary": "sum", "char_count": 10}]

    async def test_section_is_read_from_its_chunks(self, server):
        db = server._db
        sections = json.dumps([{"header": "Intro", "from_char": 0,
                                "to_char": 50}])
        doc_id = _add_doc(db, sections)
        db.add_document_chunk(doc_id, 0, "first", 0, 20)
        db.add_document_chunk(doc_id, 1, "second", 20, 40)
        text, _ = await call(server, "read_document", {
            "document_id": doc_id, "section_header": "intro"})
        assert text == "first\nsecond"

    async def test_section_without_chunks(self, server):
        sections = json.dumps([{"header": "Intro", "from_char": 0,
                                "to_char": 50}])
        doc_id = _add_doc(server._db, sections)
        text, _ = await call(server, "read_document", {
            "document_id": doc_id, "section_header": "Intro"})
        assert text == "Section found but no content."

    async def test_unknown_section_lists_the_available_ones(self, server):
        sections = json.dumps([{"header": "Intro", "from_char": 0,
                                "to_char": 5}])
        doc_id = _add_doc(server._db, sections)
        text, _ = await call(server, "read_document", {
            "document_id": doc_id, "section_header": "Methods"})
        assert text == "Section 'Methods' not found. Available: ['Intro']"

    async def test_unparseable_sections_json(self, server):
        doc_id = _add_doc(server._db, "{not json")
        text, _ = await call(server, "read_document", {
            "document_id": doc_id, "section_header": "Intro"})
        assert text == "Section 'Intro' not found. Available: []"

    async def test_section_of_missing_document(self, server):
        text, _ = await call(server, "read_document", {
            "document_id": 999, "section_header": "Intro"})
        assert text == "Error: Document not found."


# ---------------------------------------------------------------------------
# Semantic search handlers
# ---------------------------------------------------------------------------

SEARCH_TOOLS = ["search_discussions", "search_memories", "search_documents",
                "query_knowledge_graph"]


class TestSearchCommon:
    @pytest.mark.parametrize("tool", SEARCH_TOOLS)
    async def test_blank_query_is_rejected(self, server, tool):
        text, _ = await call(server, tool, {"query": "  "})
        assert text == "Error: No query provided."

    @pytest.mark.parametrize("tool", SEARCH_TOOLS)
    async def test_embedding_failure_is_reported(self, broken_embed, tool):
        text, _ = await call(broken_embed, tool, {"query": "q"})
        assert text == "Embedding service unavailable: ollama down"


class TestSearchDiscussions:
    def _seed(self, db) -> None:
        eid = _ai_entities(db, 1)[0]
        for topic, content, vec in [("Climate", "near text", NEAR),
                                    ("Budget", "far text", FAR)]:
            did = db.create_discussion(topic, eid)
            mid = db.add_message(did, eid, content, "participant")
            db.set_message_embedding(str(mid), _pack_embedding(vec))

    async def test_nothing_indexed(self, server):
        text, _ = await call(server, "search_discussions", {"query": "q"})
        assert text == "No indexed discussion messages found."

    async def test_ranks_by_similarity(self, server):
        self._seed(server._db)
        text, _ = await call(server, "search_discussions",
                             {"query": "q", "limit": 1})
        assert text == ("Found 1 relevant passages:\n\n"
                        "1. [Discussion: Climate]\n   near text")

    async def test_topic_filter(self, server):
        self._seed(server._db)
        text, _ = await call(server, "search_discussions",
                             {"query": "q", "topic_filter": "Budg"})
        assert "Budget" in text and "Climate" not in text


class TestSearchMemories:
    async def test_no_memories(self, server):
        text, _ = await call(server, "search_memories", {"query": "q"})
        assert text == "No memories found for this entity."

    async def test_unembedded_memories_are_backfilled_and_found(self, server):
        db = server._db
        agent = server._get_agent_entity_id()
        db.add_entity_memory(memory_id="m1", entity_id=agent, content="fact")
        text, _ = await call(server, "search_memories", {"query": "q"})
        assert text == "Recalled 1 memories:\n\n1. [m1] fact"
        assert server._embed_client.calls == ["q", "fact"]

    async def test_other_entity_by_id(self, server):
        db = server._db
        other = _ai_entities(db, 1)[0]
        db.add_entity_memory(memory_id="m2", entity_id=other, content="theirs")
        text, _ = await call(server, "search_memories",
                             {"query": "q", "entity_id": other})
        assert "[m2] theirs" in text


class TestSearchDocuments:
    async def test_nothing_indexed(self, server):
        text, _ = await call(server, "search_documents", {"query": "q"})
        assert text == "No indexed documents found."

    async def test_ranks_chunks(self, server):
        db = server._db
        doc_id = _add_doc(db, "[]")
        near = db.add_document_chunk(doc_id, 0, "near chunk", 0, 5)
        far = db.add_document_chunk(doc_id, 1, "far chunk", 5, 10)
        db.set_chunk_embedding(near, _pack_embedding(NEAR))
        db.set_chunk_embedding(far, _pack_embedding(FAR))
        text, _ = await call(server, "search_documents",
                             {"query": "q", "limit": 1})
        assert text.startswith("Found 1 relevant chunks:")
        assert "near chunk" in text and "far chunk" not in text


class TestKnowledgeGraph:
    async def test_neighbors_of_unknown_node(self, server):
        text, _ = await call(server, "query_knowledge_graph",
                             {"query": "X", "mode": "neighbors"})
        assert text == "No node found with label 'X'. Try mode='search'."

    async def test_isolated_node(self, server):
        server._db.upsert_kg_node("n1", "X", "concept", None)
        text, _ = await call(server, "query_knowledge_graph",
                             {"query": "X", "mode": "neighbors"})
        assert text == "Node 'X' has no connections."

    async def test_neighbors_in_both_directions(self, server):
        await call(server, "assert_knowledge",
                   {"subject": "A", "relation": "supports", "object": "B"})
        await call(server, "assert_knowledge",
                   {"subject": "C", "relation": "implies", "object": "A"})
        text, _ = await call(server, "query_knowledge_graph",
                             {"query": "A", "mode": "neighbors"})
        assert "A --[supports]--> B" in text
        assert "A <--[implies]-- C" in text

    async def test_empty_graph(self, server):
        text, _ = await call(server, "query_knowledge_graph", {"query": "q"})
        assert text == "Knowledge graph is empty."

    async def test_semantic_search_backfills_and_describes(self, server):
        server._db.upsert_kg_node("n1", "Gravity", "concept", "pulls")
        text, _ = await call(server, "query_knowledge_graph", {"query": "q"})
        assert text == ("Knowledge graph nodes related to 'q':\n\n"
                        "1. Gravity — pulls")


# ---------------------------------------------------------------------------
# Write handlers
# ---------------------------------------------------------------------------

class TestWrites:
    async def test_memory_is_stored_when_embedding_fails(self, broken_embed,
                                                         caplog):
        text, _ = await call(broken_embed, "store_memory", {"content": "keep"})
        assert text.startswith("Memory stored (id: ")
        agent = broken_embed._get_agent_entity_id()
        rows = broken_embed._db.get_unembedded_entity_memories(agent)
        assert [content for _, content in rows] == ["keep"]
        assert "Could not embed memory" in caplog.text

    async def test_stored_memory_is_embedded(self, server):
        await call(server, "store_memory", {"content": "keep"})
        agent = server._get_agent_entity_id()
        assert server._db.get_unembedded_entity_memories(agent) == []

    async def test_assert_reuses_existing_nodes(self, server):
        for _ in range(2):
            await call(server, "assert_knowledge",
                       {"subject": "A", "relation": "r", "object": "B"})
        labels = sorted(n["label"]
                        for n in server._db.get_kg_nodes_with_embeddings())
        assert labels == ["A", "B"]

    async def test_assert_survives_embedding_failure(self, broken_embed,
                                                     caplog):
        text, _ = await call(broken_embed, "assert_knowledge",
                             {"subject": "A", "relation": "r", "object": "B"})
        assert text == "Asserted: A --[r]--> B"
        assert "Could not embed KG nodes" in caplog.text

    async def test_agent_entity_is_found_by_a_new_server(self, server,
                                                         tmp_path):
        agent = server._get_agent_entity_id()
        again = ConsensusMCPServer(server._db.db_path)
        try:
            assert again._get_agent_entity_id() == agent
        finally:
            again.close()


# ---------------------------------------------------------------------------
# run_discussion end to end (fake LLM edge)
# ---------------------------------------------------------------------------

class TestRunDiscussionFlow:
    async def test_runs_and_returns_the_synthesis(self, server, llm):
        a, b = _ai_entities(server._db)
        text, is_error = await call(server, "run_discussion",
                                    {"topic": "T", "max_rounds": 1})
        data = json.loads(text)
        assert not is_error
        assert data["conclusion"].startswith("## Final Synthesis")
        assert data["conclusion_error"] == ""
        assert data["participants"] == ["AI0", "AI1"]
        assert data["topic"] == "T"
        assert data["total_messages"] > 0
        assert any(is_synthesis(c) for c in llm.calls)

    async def test_agent_entity_is_never_auto_selected(self, server, llm):
        server._get_agent_entity_id()
        _ai_entities(server._db)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert AGENT_ENTITY_NAME not in data["participants"]

    async def test_unknown_entity(self, server):
        text, _ = await call(server, "run_discussion",
                             {"topic": "T", "entity_ids": [999]})
        assert text == "Error: Entity 999 not found."

    async def test_failed_synthesis_is_reported(self, server, llm):
        _ai_entities(server._db)
        llm.fail_when = is_synthesis
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["conclusion"] == ""
        assert data["conclusion_error"]

    async def test_conclude_raising_is_reported(self, server, llm,
                                                monkeypatch):
        from consensus.app import ConsensusApp

        async def boom(self):
            raise RuntimeError("db gone")

        monkeypatch.setattr(ConsensusApp, "conclude_discussion", boom)
        _ai_entities(server._db)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["conclusion_error"] == "db gone"


# ---------------------------------------------------------------------------
# Protocol plumbing and the stdio loop
# ---------------------------------------------------------------------------

class TestToolFailure:
    async def test_handler_exception_becomes_an_error_result(self, server):
        text, is_error = await call(server, "read_discussion",
                                    {"discussion_id": "not-a-number"})
        assert is_error
        assert text.startswith("Error: ")


def _stdio(monkeypatch, lines: list[str]) -> io.StringIO:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(lines)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    return out


INIT = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "initialize"}) + "\n"
NOTIFY = json.dumps({"jsonrpc": "2.0",
                     "method": "notifications/initialized"}) + "\n"


class TestStdioLoop:
    async def test_answers_requests_and_skips_noise(self, server,
                                                    monkeypatch):
        out = _stdio(monkeypatch, ["\n", INIT, NOTIFY])
        await server.run()
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        assert [r["id"] for r in replies] == [7]
        assert replies[0]["result"]["serverInfo"]["name"] == "consensus"

    def test_main_serves_stdin_and_closes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "main.db")
        anchor = mcp_server.ConsensusMCPServer.__init__
        patch_where_defined(monkeypatch, anchor, "get_db_path",
                            lambda: db_path)
        patch_where_defined(monkeypatch, mcp_server.main, "load_env",
                            lambda: None)
        out = _stdio(monkeypatch, [INIT])
        mcp_server.main()
        assert json.loads(out.getvalue())["id"] == 7


# ---------------------------------------------------------------------------
# Defects found while writing the net (fixed after the #61 split)
# ---------------------------------------------------------------------------

class TestRunDiscussionReportsFailures:
    """A failed or stopped run must not read as a clean conclusion."""

    async def test_clean_run_has_no_errors(self, server, llm):
        _ai_entities(server._db)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["error"] == ""
        assert data["turn_errors"] == []

    async def test_skipped_turns_are_listed(self, server, llm):
        _ai_entities(server._db)
        llm.fail_when = is_turn
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert len(data["turn_errors"]) == 2
        assert data["turn_errors"][0].startswith("AI0: ")
        assert "500" in data["turn_errors"][0]

    async def test_failed_completion_stops_the_run(self, server, llm):
        """It used to retry the same turn until the 10-minute timeout."""
        _ai_entities(server._db)
        llm.fail_when = is_summary
        llm.yield_each_call = True  # lets the guard below fire if it loops
        text, _ = await asyncio.wait_for(
            call(server, "run_discussion", {"topic": "T", "max_rounds": 1}),
            timeout=10)
        data = json.loads(text)
        assert data["error"].startswith("Turn completion failed: ")
        assert sum(is_turn(c) for c in llm.calls) == 1

    async def test_timeout_is_reported(self, server, llm, monkeypatch):
        _ai_entities(server._db)
        patch_where_defined(monkeypatch,
                            ConsensusMCPServer._handle_run_discussion,
                            "RUN_DISCUSSION_TIMEOUT_SECONDS", 0)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["error"] == "Discussion timed out after 0 s"


class TestSearchDocumentsTitle:
    async def test_hit_names_its_document(self, server):
        db = server._db
        doc_id = _add_doc(db, "[]")
        chunk = db.add_document_chunk(doc_id, 0, "near chunk", 0, 5)
        db.set_chunk_embedding(chunk, _pack_embedding(NEAR))
        text, _ = await call(server, "search_documents", {"query": "q"})
        assert "1. [Document: Doc]\n   near chunk" in text


class TestStdioRobustness:
    """Malformed input gets an error reply; it must not stop the server."""

    async def _replies(self, server, monkeypatch, lines):
        out = _stdio(monkeypatch, lines + [INIT])
        await server.run()
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        assert replies[-1]["id"] == 7, "the server stopped answering"
        return replies[:-1]

    async def test_unparseable_line(self, server, monkeypatch, caplog):
        replies = await self._replies(server, monkeypatch, ["{oops\n"])
        assert [r["error"]["code"] for r in replies] == [-32700]
        assert "Unparseable JSON-RPC line" in caplog.text

    @pytest.mark.parametrize("line", [
        "[1, 2]\n",
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": None}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": [1]}) + "\n",
    ])
    async def test_malformed_request(self, server, monkeypatch, line):
        replies = await self._replies(server, monkeypatch, [line])
        assert len(replies) == 1 and "error" in replies[0]

    async def test_unknown_notification_gets_no_reply(self, server,
                                                      monkeypatch):
        line = json.dumps({"jsonrpc": "2.0",
                           "method": "notifications/cancelled"}) + "\n"
        assert await self._replies(server, monkeypatch, [line]) == []

    async def test_server_reports_the_package_version(self, server):
        import consensus
        resp = await server.handle_request(json.loads(INIT))
        assert resp["result"]["serverInfo"]["version"] == consensus.__version__
