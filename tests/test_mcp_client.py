"""Tests for MCPToolProvider — MCP client over JSON-RPC 2.0 stdin/stdout."""

import json

import pytest
from unittest.mock import AsyncMock

from consensus.mcp_client import MCPToolProvider, encode_jsonrpc_request
from consensus.tools import ToolContext


class TestMCPJsonRpc:
    def test_encode_request(self):
        msg = encode_jsonrpc_request("tools/list", {}, req_id=1)
        parsed = json.loads(msg)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "tools/list"
        assert parsed["id"] == 1

    def test_encode_request_with_meta(self):
        msg = encode_jsonrpc_request(
            "tools/call",
            {"name": "test", "arguments": {}, "_meta": {"progressToken": "tok1"}},
            req_id=2,
        )
        parsed = json.loads(msg)
        assert parsed["params"]["_meta"]["progressToken"] == "tok1"


class TestMCPToolDiscovery:
    @pytest.mark.asyncio
    async def test_list_tools_parses_response(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        mock_tools_response = {
            "tools": [{
                "name": "fact_check_claim",
                "description": "Check a medical claim",
                "inputSchema": {
                    "type": "object",
                    "properties": {"claim": {"type": "string"}},
                    "required": ["claim"],
                },
            }]
        }
        provider._send_request = AsyncMock(return_value=mock_tools_response)
        provider._connected = True
        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "fact_check_claim"
        assert tools[0].parameters["properties"]["claim"]["type"] == "string"
        await provider.close()


class TestMCPToolExecution:
    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True
        mock_result = {"content": [{"type": "text", "text": "Claim is supported."}]}
        provider._send_request = AsyncMock(return_value=mock_result)
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("fact_check", {"claim": "test"}, ctx)
        assert result.content == "Claim is supported."
        assert result.is_error is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_with_progress_callback(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True
        progress_events = []

        async def mock_send_with_progress(method, params, timeout=None, progress_callback=None):
            if progress_callback:
                progress_callback(1, 10, "Searching...")
                progress_callback(5, 10, "Scoring...")
            return {"content": [{"type": "text", "text": "Done"}]}

        provider._send_request = mock_send_with_progress
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        cb = lambda p, t, m: progress_events.append((p, t, m))
        result = await provider.execute("tool", {}, ctx, progress_callback=cb)
        assert len(progress_events) == 2
        assert progress_events[0] == (1, 10, "Searching...")
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_error_result(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = True
        mock_result = {"content": [{"type": "text", "text": "Error"}], "isError": True}
        provider._send_request = AsyncMock(return_value=mock_result)
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("tool", {}, ctx)
        assert result.is_error is True
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_not_connected(self):
        provider = MCPToolProvider(name="test", command="echo", args=[])
        provider._connected = False
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("tool", {}, ctx)
        assert result.is_error is True
        assert "not connected" in result.content.lower()
        await provider.close()
