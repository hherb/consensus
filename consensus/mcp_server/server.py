"""MCP server exposing Consensus data and operations to external AI agents.

Implements the Model Context Protocol (JSON-RPC 2.0 over stdio) so that
tools like Claude Code can search discussion history, query the knowledge
graph, store/recall persistent memories, and even trigger full discussions.

Usage:
    consensus-mcp              # entry point via pyproject.toml
    python -m consensus.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Optional

from ..config import get_db_path, load_env
from ..database import Database
from .constants import (
    AGENT_ENTITY_NAME, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST,
    JSONRPC_VERSION, MCP_PROTOCOL_VERSION, METHOD_NOT_FOUND, PARSE_ERROR,
    SERVER_NAME, SERVER_VERSION,
)
from .handlers_read import ReadHandlersMixin
from .handlers_write import WriteHandlersMixin
from .tool_defs import ToolRegistrationMixin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class ConsensusMCPServer(ToolRegistrationMixin, ReadHandlersMixin,
                         WriteHandlersMixin):
    """MCP server exposing Consensus data and operations."""

    def __init__(self, db_path: str = "") -> None:
        self._db = Database(db_path or get_db_path())
        self._embed_client = None  # lazy
        self._agent_entity_id: Optional[int] = None
        self._tools: dict[str, dict] = {}
        self._register_all_tools()

    # -- Lazy helpers -------------------------------------------------------

    def _get_embed_client(self):
        if self._embed_client is None:
            from ..tools_memory import EmbeddingClient
            self._embed_client = EmbeddingClient(self._db)
        return self._embed_client

    def _get_agent_entity_id(self) -> int:
        if self._agent_entity_id is not None:
            return self._agent_entity_id
        for e in self._db.get_entities(entity_type="ai"):
            if e["name"] == AGENT_ENTITY_NAME:
                self._agent_entity_id = e["id"]
                return self._agent_entity_id
        self._agent_entity_id = self._db.add_entity(
            name=AGENT_ENTITY_NAME, entity_type="ai")
        return self._agent_entity_id

    def _resolve_entity_id(self, raw: int) -> int:
        """Resolve entity_id=0 to the agent's own entity."""
        return self._get_agent_entity_id() if raw == 0 else int(raw)

    # -----------------------------------------------------------------------
    # JSON-RPC protocol
    # -----------------------------------------------------------------------

    def _make_response(self, msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}

    def _make_error(self, msg_id: Any, code: int, message: str) -> dict:
        return {
            "jsonrpc": JSONRPC_VERSION, "id": msg_id,
            "error": {"code": code, "message": message},
        }

    async def handle_request(self, msg: Any) -> Optional[dict]:
        """Answer one decoded JSON-RPC message; ``None`` for a notification."""
        if not isinstance(msg, dict):
            return self._make_error(None, INVALID_REQUEST,
                                    "Invalid Request: expected a JSON object")
        # Checked before anything else: JSON-RPC forbids answering a
        # notification, even an ``initialize`` or a malformed one.
        if "id" not in msg:
            return None
        method = msg.get("method")
        msg_id = msg["id"]
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._make_error(msg_id, INVALID_PARAMS,
                                    "Invalid params: expected an object")

        if method == "initialize":
            return self._make_response(msg_id, {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })

        if method == "tools/list":
            tools = [t["definition"] for t in self._tools.values()]
            return self._make_response(msg_id, {"tools": tools})

        if method == "tools/call":
            return await self._handle_tool_call(msg_id, params)

        return self._make_error(msg_id, METHOD_NOT_FOUND,
                                f"Method not found: {method}")

    async def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        """Run the named tool; a tool failure is an ``isError`` result."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(tool_name, str):
            return self._make_error(msg_id, INVALID_PARAMS,
                                    "Invalid params: name must be a string")
        if not isinstance(arguments, dict):
            return self._make_error(msg_id, INVALID_PARAMS,
                                    "Invalid params: arguments must be an object")
        tool = self._tools.get(tool_name)
        if not tool:
            return self._make_error(msg_id, INVALID_PARAMS,
                                    f"Unknown tool: {tool_name}")
        try:
            result_text = await tool["handler"](arguments)
            return self._make_response(msg_id, {
                "content": [{"type": "text", "text": result_text}],
            })
        except Exception as e:
            from ..app_discussion_flow.helpers import describe_flow_error
            logger.exception("Tool %s failed", tool_name)
            return self._make_response(msg_id, {
                "content": [{"type": "text",
                             "text": f"Error: {describe_flow_error(e)}"}],
                "isError": True,
            })

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    async def run(self) -> None:
        """Read JSON-RPC from stdin, dispatch, write responses to stdout.

        Uses synchronous stdin reads in a thread executor to avoid asyncio
        pipe transport issues on macOS/redirected pipes, while keeping tool
        handlers fully async.
        """
        loop = asyncio.get_running_loop()

        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("Unparseable JSON-RPC line: %s", e)
                response = self._make_error(None, PARSE_ERROR,
                                            f"Parse error: {e}")
            else:
                response = await self._dispatch(msg)
            if response is not None:
                out = json.dumps(response) + "\n"
                sys.stdout.write(out)
                sys.stdout.flush()

    async def _dispatch(self, msg: Any) -> Optional[dict]:
        """``handle_request``, with a bug in it answered rather than fatal."""
        try:
            return await self.handle_request(msg)
        except Exception:
            logger.exception("Unhandled error dispatching a JSON-RPC message")
            if isinstance(msg, dict) and "id" in msg:
                return self._make_error(msg["id"], INTERNAL_ERROR,
                                        "Internal error")
            return None

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()


def main() -> None:
    load_env()
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    server = ConsensusMCPServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    finally:
        server.close()

