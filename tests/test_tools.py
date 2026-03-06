"""Tests for consensus.tools — registry, access control, execution."""

import asyncio

import pytest

from consensus.tools import (
    ToolDefinition, ToolResult, ToolCallRecord, ToolContext,
    PythonToolProvider, ToolRegistry, TOOL_EXECUTION_TIMEOUT,
)


# --- ToolDefinition ---

class TestToolDefinition:
    def test_to_openai_schema(self):
        td = ToolDefinition(
            name="search", description="Search the web",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        schema = td.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "search"
        assert "properties" in schema["function"]["parameters"]


# --- ToolCallRecord ---

class TestToolCallRecord:
    def test_roundtrip(self):
        rec = ToolCallRecord(
            tool_name="calc", arguments={"x": 1}, result="2",
            is_error=False, latency_ms=50,
        )
        d = rec.to_dict()
        rebuilt = ToolCallRecord.from_dict(d)
        assert rebuilt.tool_name == "calc"
        assert rebuilt.arguments == {"x": 1}
        assert rebuilt.result == "2"


# --- PythonToolProvider ---

class TestPythonToolProvider:
    @pytest.fixture
    def provider(self):
        p = PythonToolProvider("test")
        td = ToolDefinition(
            name="add", description="Add two numbers",
            parameters={"type": "object", "properties": {
                "a": {"type": "number"}, "b": {"type": "number"},
            }},
        )
        p.register(td, lambda args, ctx: ToolResult(content=str(args["a"] + args["b"])))
        return p

    @pytest.mark.asyncio
    async def test_list_tools(self, provider):
        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "add"

    @pytest.mark.asyncio
    async def test_execute_success(self, provider):
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("add", {"a": 3, "b": 4}, ctx)
        assert result.content == "7"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, provider):
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("nonexistent", {}, ctx)
        assert result.is_error is True
        assert "Unknown" in result.content

    @pytest.mark.asyncio
    async def test_execute_handler_exception(self):
        p = PythonToolProvider("test")
        td = ToolDefinition(name="fail", description="Always fails",
                            parameters={"type": "object"})
        p.register(td, lambda args, ctx: 1/0)
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await p.execute("fail", {}, ctx)
        assert result.is_error is True
        assert "error" in result.content.lower()

    @pytest.mark.asyncio
    async def test_execute_async_handler(self):
        p = PythonToolProvider("test")
        td = ToolDefinition(name="async_tool", description="Async tool",
                            parameters={"type": "object"})

        async def handler(args, ctx):
            return ToolResult(content="async result")

        p.register(td, handler)
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await p.execute("async_tool", {}, ctx)
        assert result.content == "async result"

    @pytest.mark.asyncio
    async def test_execute_returning_string(self):
        """Handler returning a plain string should be wrapped in ToolResult."""
        p = PythonToolProvider("test")
        td = ToolDefinition(name="plain", description="Returns string",
                            parameters={"type": "object"})
        p.register(td, lambda args, ctx: "plain text")
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await p.execute("plain", {}, ctx)
        assert result.content == "plain text"
        assert result.is_error is False


# --- ToolRegistry ---

class TestToolRegistry:
    @pytest.fixture
    def registry_with_tools(self, tmp_db):
        registry = ToolRegistry(db=tmp_db)
        p = PythonToolProvider("builtin")
        td1 = ToolDefinition(name="search", description="Search",
                             parameters={"type": "object"})
        td2 = ToolDefinition(name="calc", description="Calculate",
                             parameters={"type": "object"})
        p.register(td1, lambda args, ctx: ToolResult(content="found"))
        p.register(td2, lambda args, ctx: ToolResult(content="42"))
        registry.register_provider(p)
        return registry

    @pytest.mark.asyncio
    async def test_list_all_tools(self, registry_with_tools):
        tools = await registry_with_tools.list_all_tools()
        names = [t.name for t in tools]
        assert "search" in names
        assert "calc" in names

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, registry_with_tools):
        result = await registry_with_tools.execute(
            "nonexistent", {}, caller_entity_id=1,
            discussion_id=1,
        )
        assert result.is_error is True
        assert "Unknown" in result.content

    @pytest.mark.asyncio
    async def test_execute_without_assignment_denied(self, registry_with_tools, tmp_db):
        """Entity without tool assignment should be denied access."""
        eid = tmp_db.add_entity("Bot", "human", "#000")
        did = tmp_db.create_discussion("T", eid)
        result = await registry_with_tools.execute(
            "search", {}, caller_entity_id=eid,
            discussion_id=did,
        )
        assert result.is_error is True
        assert "denied" in result.content.lower() or "not assigned" in result.content.lower()

    @pytest.mark.asyncio
    async def test_execute_with_assignment_succeeds(self, registry_with_tools, tmp_db):
        eid = tmp_db.add_entity("Bot", "human", "#000")
        did = tmp_db.create_discussion("T", eid)
        tmp_db.add_entity_tool(eid, "search", "private")
        result = await registry_with_tools.execute(
            "search", {}, caller_entity_id=eid,
            discussion_id=did,
        )
        assert result.is_error is False
        assert result.content == "found"

    @pytest.mark.asyncio
    async def test_moderator_only_access_denied(self, registry_with_tools, tmp_db):
        eid = tmp_db.add_entity("Bot", "human", "#000")
        mod_id = tmp_db.add_entity("Mod", "human", "#111")
        did = tmp_db.create_discussion("T", mod_id)
        tmp_db.add_entity_tool(eid, "search", "moderator_only")
        result = await registry_with_tools.execute(
            "search", {}, caller_entity_id=eid,
            discussion_id=did, moderator_id=mod_id,
        )
        assert result.is_error is True
        assert "moderator" in result.content.lower()

    @pytest.mark.asyncio
    async def test_moderator_only_access_allowed_for_moderator(self, registry_with_tools, tmp_db):
        mod_id = tmp_db.add_entity("Mod", "human", "#111")
        did = tmp_db.create_discussion("T", mod_id)
        tmp_db.add_entity_tool(mod_id, "search", "moderator_only")
        result = await registry_with_tools.execute(
            "search", {}, caller_entity_id=mod_id,
            discussion_id=did, moderator_id=mod_id,
        )
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tmp_db):
        registry = ToolRegistry(db=tmp_db)
        p = PythonToolProvider("slow")
        td = ToolDefinition(name="slow_tool", description="Slow",
                            parameters={"type": "object"})

        async def slow_handler(args, ctx):
            await asyncio.sleep(100)
            return ToolResult(content="done")

        p.register(td, slow_handler)
        registry.register_provider(p)

        eid = tmp_db.add_entity("Bot", "human", "#000")
        did = tmp_db.create_discussion("T", eid)
        tmp_db.add_entity_tool(eid, "slow_tool", "private")

        # Monkey-patch timeout to avoid waiting 30s in tests
        import consensus.tools
        orig = consensus.tools.TOOL_EXECUTION_TIMEOUT
        consensus.tools.TOOL_EXECUTION_TIMEOUT = 0.1
        try:
            result = await registry.execute(
                "slow_tool", {}, caller_entity_id=eid,
                discussion_id=did,
            )
            assert result.is_error is True
            assert "timed out" in result.content.lower()
        finally:
            consensus.tools.TOOL_EXECUTION_TIMEOUT = orig

    @pytest.mark.asyncio
    async def test_get_tools_for_entity_respects_overrides(self, registry_with_tools, tmp_db):
        eid = tmp_db.add_entity("Bot", "human", "#000")
        did = tmp_db.create_discussion("T", eid)
        tmp_db.add_entity_tool(eid, "search", "private")
        tmp_db.add_entity_tool(eid, "calc", "private")
        # Disable "calc" for this discussion
        tmp_db.set_discussion_tool_override(did, eid, "calc", False)

        tools = await registry_with_tools.get_tools_for_entity(eid, did)
        names = [t.name for t in tools]
        assert "search" in names
        assert "calc" not in names
