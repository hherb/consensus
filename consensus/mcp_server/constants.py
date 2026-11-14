"""Protocol constants of the Consensus MCP server."""

from .. import __version__

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "consensus"
SERVER_VERSION = __version__

AGENT_ENTITY_NAME = "Claude Code Agent"

#: ``run_discussion`` defaults when the caller leaves a parameter out.
DEFAULT_MAX_ROUNDS = 3
DEFAULT_COST_LIMIT_USD = 1.0
#: How many AI entities ``run_discussion`` picks when none are named.
AUTO_SELECT_ENTITIES = 3
#: Wall-clock cap on one ``run_discussion`` turn loop.
RUN_DISCUSSION_TIMEOUT_SECONDS = 600
