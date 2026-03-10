"""Tests for the Consensus MCP server."""

import json
import uuid

import pytest

from consensus.mcp_server import ConsensusMCPServer, JSONRPC_VERSION, MCP_PROTOCOL_VERSION


@pytest.fixture
def server(tmp_path):
    """Create a ConsensusMCPServer backed by a temporary database."""
    db_path = str(tmp_path / "test.db")
    srv = ConsensusMCPServer(db_path)
    yield srv
    srv.close()


@pytest.fixture
def server_with_entities(server):
    """Server with two AI entities and a provider configured."""
    db = server._db
    prov_id = db.add_provider("TestProvider", "http://localhost:11434/v1", "TEST_KEY")
    eid1 = db.add_entity("Alice", "ai", "#ff0000", prov_id, "test-model", 0.5, 512, "You are Alice.")
    eid2 = db.add_entity("Bob", "ai", "#0000ff", prov_id, "test-model", 0.7, 512, "You are Bob.")
    return server, eid1, eid2, prov_id


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------

class TestProtocol:
    @pytest.mark.asyncio
    async def test_initialize(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        assert resp["jsonrpc"] == JSONRPC_VERSION
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "consensus"

    @pytest.mark.asyncio
    async def test_initialized_notification(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        assert resp is None  # notifications get no response

    @pytest.mark.asyncio
    async def test_tools_list(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "list_discussions" in names
        assert "read_discussion" in names
        assert "search_discussions" in names
        assert "list_entities" in names
        assert "search_memories" in names
        assert "query_knowledge_graph" in names
        assert "list_documents" in names
        assert "read_document" in names
        assert "search_documents" in names
        assert "store_memory" in names
        assert "delete_memory" in names
        assert "assert_knowledge" in names
        assert "run_discussion" in names
        assert len(names) == 13

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "foo/bar", "params": {},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_unknown_tool(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# Passive tool tests
# ---------------------------------------------------------------------------

class TestListTools:
    @pytest.mark.asyncio
    async def test_list_discussions_empty(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "list_discussions", "arguments": {}},
        })
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text)
        assert data == []

    @pytest.mark.asyncio
    async def test_list_discussions_with_data(self, server_with_entities):
        server, eid1, eid2, _ = server_with_entities
        db = server._db
        db.create_discussion("Test topic", eid1)
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "list_discussions", "arguments": {}},
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["topic"] == "Test topic"

    @pytest.mark.asyncio
    async def test_list_discussions_filter(self, server_with_entities):
        server, eid1, eid2, _ = server_with_entities
        db = server._db
        db.create_discussion("Topic A", eid1)
        disc_id = db.create_discussion("Topic B", eid2)
        db.update_discussion(disc_id, status="active")
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "list_discussions",
                       "arguments": {"status": "active"}},
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["topic"] == "Topic B"

    @pytest.mark.asyncio
    async def test_list_entities(self, server_with_entities):
        server, eid1, eid2, _ = server_with_entities
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "list_entities", "arguments": {}},
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        names = {e["name"] for e in data}
        assert "Alice" in names
        assert "Bob" in names

    @pytest.mark.asyncio
    async def test_list_entities_filter(self, server_with_entities):
        server, eid1, eid2, _ = server_with_entities
        db = server._db
        db.add_entity("Human", "human")
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 14, "method": "tools/call",
            "params": {"name": "list_entities",
                       "arguments": {"entity_type": "human"}},
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        assert len(data) == 1
        assert data[0]["name"] == "Human"

    @pytest.mark.asyncio
    async def test_list_documents_empty(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "list_documents", "arguments": {}},
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        assert data == []


class TestReadTools:
    @pytest.mark.asyncio
    async def test_read_discussion(self, server_with_entities):
        server, eid1, eid2, _ = server_with_entities
        db = server._db
        disc_id = db.create_discussion("Read test", eid1)
        db.add_discussion_member(disc_id, eid1, is_moderator=True)
        db.add_discussion_member(disc_id, eid2, turn_position=0)
        db.add_message(disc_id, eid2, "Hello!", "participant", turn_number=1)
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "read_discussion",
                       "arguments": {"discussion_id": disc_id}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Read test" in text
        assert "Hello!" in text

    @pytest.mark.asyncio
    async def test_read_discussion_not_found(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {"name": "read_discussion",
                       "arguments": {"discussion_id": 9999}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text

    @pytest.mark.asyncio
    async def test_read_document(self, server):
        db = server._db
        doc_id = db.add_document(
            "test.md", "Test Doc", "A test document",
            "text/markdown", "text", None, "# Hello\n\nWorld", 15, "[]",
        )
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {"name": "read_document",
                       "arguments": {"document_id": doc_id}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Hello" in text
        assert "World" in text

    @pytest.mark.asyncio
    async def test_read_document_not_found(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 23, "method": "tools/call",
            "params": {"name": "read_document",
                       "arguments": {"document_id": 9999}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text or "not found" in text.lower()


# ---------------------------------------------------------------------------
# Write tool tests
# ---------------------------------------------------------------------------

class TestWriteTools:
    @pytest.mark.asyncio
    async def test_store_memory(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 30, "method": "tools/call",
            "params": {"name": "store_memory",
                       "arguments": {"content": "Test memory content"}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Memory stored" in text
        # Verify agent entity was created
        agent_id = server._get_agent_entity_id()
        assert agent_id > 0
        # Verify the entity is named correctly
        entity = server._db.get_entity(agent_id)
        assert entity["name"] == "Claude Code Agent"

    @pytest.mark.asyncio
    async def test_store_memory_empty(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {"name": "store_memory",
                       "arguments": {"content": ""}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text

    @pytest.mark.asyncio
    async def test_delete_memory_own(self, server):
        # Store a memory first
        agent_id = server._get_agent_entity_id()
        memory_id = str(uuid.uuid4())
        server._db.add_entity_memory(memory_id, agent_id, "deletable memory")
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 32, "method": "tools/call",
            "params": {"name": "delete_memory",
                       "arguments": {"memory_id": memory_id}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "deleted" in text.lower()

    @pytest.mark.asyncio
    async def test_delete_memory_other_entity_blocked(self, server):
        """Cannot delete another entity's memory via delete_memory."""
        db = server._db
        other_id = db.add_entity("OtherEntity", "ai")
        memory_id = str(uuid.uuid4())
        db.add_entity_memory(memory_id, other_id, "other's memory")
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 33, "method": "tools/call",
            "params": {"name": "delete_memory",
                       "arguments": {"memory_id": memory_id}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "not found" in text.lower() or "does not belong" in text.lower()

    @pytest.mark.asyncio
    async def test_delete_memory_nonexistent(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 34, "method": "tools/call",
            "params": {"name": "delete_memory",
                       "arguments": {"memory_id": "nonexistent-id"}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "not found" in text.lower() or "does not belong" in text.lower()

    @pytest.mark.asyncio
    async def test_assert_knowledge(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 35, "method": "tools/call",
            "params": {"name": "assert_knowledge",
                       "arguments": {"subject": "Python", "relation": "is_a",
                                     "object": "Programming Language"}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Asserted" in text
        assert "Python" in text
        assert "Programming Language" in text

    @pytest.mark.asyncio
    async def test_assert_knowledge_missing_fields(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 36, "method": "tools/call",
            "params": {"name": "assert_knowledge",
                       "arguments": {"subject": "Python", "relation": ""}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text


class TestAgentEntity:
    @pytest.mark.asyncio
    async def test_agent_entity_created_once(self, server):
        """Agent entity is created on first access and reused."""
        id1 = server._get_agent_entity_id()
        id2 = server._get_agent_entity_id()
        assert id1 == id2
        assert id1 > 0

    @pytest.mark.asyncio
    async def test_resolve_entity_id_zero(self, server):
        """entity_id=0 resolves to the agent's own entity."""
        resolved = server._resolve_entity_id(0)
        agent_id = server._get_agent_entity_id()
        assert resolved == agent_id

    @pytest.mark.asyncio
    async def test_resolve_entity_id_nonzero(self, server):
        """entity_id != 0 passes through unchanged."""
        assert server._resolve_entity_id(42) == 42


class TestRunDiscussion:
    @pytest.mark.asyncio
    async def test_run_discussion_no_entities(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 40, "method": "tools/call",
            "params": {"name": "run_discussion",
                       "arguments": {"topic": "Test topic"}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text
        assert "2 AI entities" in text

    @pytest.mark.asyncio
    async def test_run_discussion_human_rejected(self, server):
        db = server._db
        prov_id = db.add_provider("P", "http://localhost/v1", "KEY")
        ai_id = db.add_entity("AI1", "ai", "#000", prov_id, "m", 0.5, 512, "")
        human_id = db.add_entity("Human1", "human")
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {"name": "run_discussion",
                       "arguments": {"topic": "Test", "entity_ids": [ai_id, human_id]}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "human" in text.lower()

    @pytest.mark.asyncio
    async def test_run_discussion_empty_topic(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {"name": "run_discussion",
                       "arguments": {"topic": ""}},
        })
        text = resp["result"]["content"][0]["text"]
        assert "Error" in text
