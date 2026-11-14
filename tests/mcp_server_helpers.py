"""Shared fixtures and helpers for the MCP server test modules.

``tests/test_mcp_server_handlers.py`` and ``tests/test_mcp_server_robustness.py``
both drive a real :class:`ConsensusMCPServer` on a temporary database through
``handle_request`` or the stdio loop; this module holds what they share.
"""

import io
import json
import sys

import pytest

from consensus.mcp_server import ConsensusMCPServer
from tests.document_helpers import FakeEmbedClient

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


def _stdio(monkeypatch, lines: list[str]) -> io.StringIO:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(lines)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    return out


INIT = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "initialize"}) + "\n"
NOTIFY = json.dumps({"jsonrpc": "2.0",
                     "method": "notifications/initialized"}) + "\n"
