"""Integration test for MCP expert consultation flow."""

import asyncio
import json
import sys
import pytest
from pathlib import Path

from consensus.mcp_client import MCPToolProvider
from consensus.tools import ToolContext


@pytest.fixture
def mock_server_script(tmp_path):
    """Create a minimal MCP server script for testing."""
    script = tmp_path / "mock_mcp_server.py"
    script.write_text('''
import json
import sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        msg = json.loads(line.strip())

        if msg.get("method") == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-server", "version": "1.0"},
            }})
        elif msg.get("method") == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "test_tool", "description": "A test tool",
                 "inputSchema": {"type": "object", "properties": {
                     "query": {"type": "string"}
                 }, "required": ["query"]}},
            ]}})
        elif msg.get("method") == "tools/call":
            token = msg.get("params", {}).get("_meta", {}).get("progressToken")
            if token:
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": 1, "total": 2,
                                 "message": "Step 1..."}})
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": 2, "total": 2,
                                 "message": "Step 2..."}})
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "Test result for: " + msg["params"]["arguments"].get("query", "")}],
            }})

if __name__ == "__main__":
    main()
''')
    return str(script)


class TestMCPIntegration:
    @pytest.mark.asyncio
    async def test_connect_and_list_tools(self, mock_server_script):
        provider = MCPToolProvider(
            name="test", command=sys.executable, args=[mock_server_script],
        )
        await provider.connect()
        assert provider._connected is True

        tools = await provider.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test_tool"
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_with_progress(self, mock_server_script):
        provider = MCPToolProvider(
            name="test", command=sys.executable, args=[mock_server_script],
        )
        await provider.connect()

        progress_events = []
        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute(
            "test_tool", {"query": "hello"},
            ctx, progress_callback=lambda p, t, m: progress_events.append((p, t, m)),
        )
        assert "hello" in result.content
        assert len(progress_events) == 2
        assert progress_events[0] == (1, 2, "Step 1...")
        assert progress_events[1] == (2, 2, "Step 2...")
        await provider.close()

    @pytest.mark.asyncio
    async def test_execute_without_progress(self, mock_server_script):
        provider = MCPToolProvider(
            name="test", command=sys.executable, args=[mock_server_script],
        )
        await provider.connect()

        ctx = ToolContext(caller_entity_id=1, discussion_id=1)
        result = await provider.execute("test_tool", {"query": "world"}, ctx)
        assert "world" in result.content
        assert result.is_error is False
        await provider.close()
