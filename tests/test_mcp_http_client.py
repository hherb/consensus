"""Tests for MCPHTTPToolProvider — MCP client over Streamable HTTP."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.mcp_http_client import MCPHTTPToolProvider
from consensus.tools import ToolContext


class TestMCPHTTPInit:
    """Test HTTP provider initialization."""

    def test_init_stores_url_and_headers(self):
        provider = MCPHTTPToolProvider(
            name="test",
            url="https://mcp.example.com/mcp",
            headers={"Authorization": "Bearer tok"},
        )
        assert provider._url == "https://mcp.example.com/mcp"
        assert provider._headers["Authorization"] == "Bearer tok"
        assert provider._connected is False


class TestMCPHTTPToolDiscovery:
    """Test tool listing over HTTP."""

    @pytest.mark.asyncio
    async def test_list_tools_parses_response(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        provider._connected = True

        mock_response = {
            "tools": [{
                "name": "search",
                "description": "Search the web",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }]
        }
        provider._send_request = AsyncMock(return_value=mock_response)

        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].parameters["required"] == ["query"]
        await provider.close()


class TestMCPHTTPToolExecution:
    """Test tool execution over HTTP."""

    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        provider._connected = True

        mock_result = {"content": [{"type": "text", "text": "Result text"}]}
        provider._send_request = AsyncMock(return_value=mock_result)

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("search", {"query": "test"}, ctx)
        assert result.content == "Result text"
        assert result.is_error is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_not_connected(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        provider._connected = False

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("search", {"query": "test"}, ctx)
        assert result.is_error is True
        assert "not connected" in result.content.lower()
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_error_result(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        provider._connected = True

        mock_result = {"content": [{"type": "text", "text": "Error"}], "isError": True}
        provider._send_request = AsyncMock(return_value=mock_result)

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("search", {}, ctx)
        assert result.is_error is True
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_with_progress_callback(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        provider._connected = True
        progress_events = []

        async def mock_send(method, params, timeout=None, progress_callback=None):
            if progress_callback:
                progress_callback(1, 5, "Working...")
            return {"content": [{"type": "text", "text": "Done"}]}

        provider._send_request = mock_send

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        cb = lambda p, t, m: progress_events.append((p, t, m))
        result = await provider.execute("tool", {}, ctx, progress_callback=cb)
        assert len(progress_events) == 1
        await provider.close()


class TestMCPHTTPSessionManagement:
    """Test session ID handling for Streamable HTTP."""

    def test_session_id_initially_none(self):
        provider = MCPHTTPToolProvider(name="test", url="https://example.com/mcp")
        assert provider._session_id is None
