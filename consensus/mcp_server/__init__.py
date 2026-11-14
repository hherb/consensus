"""MCP server exposing Consensus data and operations to external AI agents.

The ``consensus-mcp`` entry point and ``python -m consensus.mcp_server`` both
run :func:`main`. The package is split by concern (issue #61): ``server`` holds
the class, JSON-RPC dispatch and stdio loop; ``tool_defs`` the advertised tool
schemas; ``handlers_read`` / ``handlers_write`` the tool handlers as mixins.
"""

from .constants import (
    AGENT_ENTITY_NAME, JSONRPC_VERSION, MCP_PROTOCOL_VERSION, SERVER_NAME,
    SERVER_VERSION,
)
from .server import ConsensusMCPServer, main

__all__ = [
    "AGENT_ENTITY_NAME",
    "ConsensusMCPServer",
    "JSONRPC_VERSION",
    "MCP_PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "main",
]
