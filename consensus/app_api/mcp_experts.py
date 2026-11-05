"""ConsensusApp mixin: MCP server management, expert definitions and expert consultation.

Moved from ``consensus/app.py`` (issue #61); the error paths of
``test_mcp_connection`` and ``_handle_consult_expert`` have since gained
logging. The host class is ``ConsensusApp``, which owns all state.
"""

import asyncio
import logging
import time

import httpx

from ..models import Message, MessageRole
from ..tools import ToolContext, ToolResult


logger = logging.getLogger(__name__)


class MCPExpertsMixin:
    """MCP server management, expert definitions and expert consultation.

    Expects the host class to provide: db, discussion, tool_registry,
    _mcp_providers, _notify(), emit().
    """

    # ------------------------------------------------------------------
    # MCP Server Management
    # ------------------------------------------------------------------

    def add_mcp_server(self, name: str, description: str, command: str,
                       args: list | None = None, env: dict | None = None,
                       transport: str = "stdio", url: str = "",
                       headers: dict | None = None) -> dict | None:
        """Register an MCP server and return its record."""
        server_id = self.db.add_mcp_server(
            name, description, command, args, env,
            transport=transport, url=url, headers=headers,
        )
        self._notify()
        return self.db.get_mcp_server(server_id)

    def get_mcp_servers(self) -> list[dict]:
        """Return all registered MCP servers."""
        return self.db.get_mcp_servers()

    def update_mcp_server(self, server_id: int, **kwargs) -> None:
        """Update an MCP server's configuration."""
        self.db.update_mcp_server(server_id, **kwargs)
        self._notify()

    async def delete_mcp_server(self, server_id: int) -> None:
        """Delete an MCP server, closing any active connection."""
        if server_id in self._mcp_providers:
            await self._mcp_providers[server_id].close()
            del self._mcp_providers[server_id]
        self.db.delete_mcp_server(server_id)
        self._notify()

    @staticmethod
    def _create_mcp_provider(server: dict):
        """Create the appropriate MCP tool provider for a server record."""
        transport = server.get("transport", "stdio")
        if transport == "http":
            from ..mcp_http_client import MCPHTTPToolProvider
            return MCPHTTPToolProvider(
                name=server["name"],
                url=server.get("url", ""),
                headers=server.get("headers", {}),
            )
        from ..mcp_client import MCPToolProvider
        return MCPToolProvider(
            name=server["name"], command=server["command"],
            args=server["args"], env=server["env"],
        )

    async def test_mcp_connection(self, server_id: int) -> dict:
        """Test connectivity to an MCP server and list its tools."""
        server = self.db.get_mcp_server(server_id)
        if not server:
            return {"success": False, "error": "Server not found"}

        provider = self._create_mcp_provider(server)

        try:
            if not await provider.connect():
                # connect() has already logged the traceback.
                reason = provider.last_error
                return {"success": False,
                        "error": f"Failed to connect: {reason}" if reason
                        else "Failed to connect"}
            tools = await provider.list_tools()
            tool_info = [{"name": t.name, "description": t.description} for t in tools]
            return {"success": True, "tools": tool_info}
        except (OSError, RuntimeError, httpx.HTTPError) as e:
            # Expected faults (timeout, dropped pipe, JSON-RPC error); a bare
            # TimeoutError has an empty str(), hence the type-name fallback.
            error = str(e) or type(e).__name__
            logger.warning("MCP connection test failed for server %r: %s",
                           server["name"], error)
            return {"success": False, "error": error}
        except Exception as e:
            # A bug in the client, not the server: keep the traceback.
            logger.exception("MCP connection test failed unexpectedly for server %r",
                             server["name"])
            return {"success": False, "error": str(e) or type(e).__name__}
        finally:
            await provider.close()

    async def connect_mcp_server(self, server_id: int) -> bool:
        """Connect to an MCP server and register it as a tool provider."""
        server = self.db.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            return False

        provider = self._create_mcp_provider(server)

        if not await provider.connect():
            return False
        self._mcp_providers[server_id] = provider
        self.tool_registry.register_provider(provider)
        return True

    # ------------------------------------------------------------------
    # Expert Definitions
    # ------------------------------------------------------------------

    def save_expert_definition(self, entity_id: int, mcp_server_id: int, tool_name: str,
                               description: str = "", default_arguments: dict | None = None,
                               query_param_name: str = "query",
                               timeout_seconds: int = 300) -> dict | None:
        """Save an expert definition linking an entity to an MCP tool."""
        self.db.add_expert_definition(entity_id, mcp_server_id, tool_name,
                                       description, default_arguments,
                                       query_param_name, timeout_seconds)
        self._notify()
        return self.db.get_expert_definition(entity_id)

    def get_expert_definitions(self) -> list[dict]:
        """Return all expert definitions."""
        return self.db.get_expert_definitions()

    # ------------------------------------------------------------------
    # Consult Expert
    # ------------------------------------------------------------------

    async def _handle_list_experts(self, args: dict, context: ToolContext) -> ToolResult:
        """Tool handler: list available specialist experts."""
        entities = self.db.get_entities()
        experts = [e for e in entities if e.get("entity_type") == "expert"]
        if not experts:
            return ToolResult(content="No experts are currently configured.")
        lines = []
        for e in experts:
            defn = self.db.get_expert_definition(e["id"])
            desc = defn.get("description", "") if defn else ""
            status = "ready" if defn and self._mcp_providers.get(defn["mcp_server_id"]) else "unavailable"
            entry = f"- **{e['name']}** [{status}]"
            if desc:
                entry += f": {desc}"
            lines.append(entry)
        return ToolResult(content="Available experts:\n" + "\n".join(lines))

    async def _handle_consult_expert(self, args: dict, context: ToolContext) -> ToolResult:
        """Tool handler: consult a specialist expert via its MCP server."""
        expert_name = args.get("expert_name", "")
        query = args.get("query", "")

        # Find expert entity by name
        entities = self.db.get_entities()
        expert_entity = None
        for e in entities:
            if e["name"].lower() == expert_name.lower() and e.get("entity_type") == "expert":
                expert_entity = e
                break

        if not expert_entity:
            available = [e["name"] for e in entities if e.get("entity_type") == "expert"]
            return ToolResult(
                content=f"Expert '{expert_name}' not found. Available: {', '.join(available) or 'none'}",
                is_error=True,
            )

        defn = self.db.get_expert_definition(expert_entity["id"])
        if not defn:
            return ToolResult(content=f"Expert '{expert_name}' has no MCP configuration", is_error=True)

        provider = self._mcp_providers.get(defn["mcp_server_id"])
        if not provider:
            return ToolResult(content=f"MCP server for '{expert_name}' is not available", is_error=True)

        # Build arguments — use configurable param name (defaults to "query")
        tool_args = dict(defn["default_arguments"])
        param_name = defn.get("query_param_name", "query")
        tool_args[param_name] = query

        def on_progress(progress, total, message):
            self.emit("tool_progress", {
                "discussion_id": context.discussion_id,
                "entity_name": expert_entity["name"],
                "tool_name": defn["tool_name"],
                "progress": progress,
                "total": total,
                "message": message,
            })

        timeout = defn.get("timeout_seconds", 300)
        try:
            result = await asyncio.wait_for(
                provider.execute(defn["tool_name"], tool_args, context, progress_callback=on_progress),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Expert %r timed out after %ss", expert_name, timeout)
            return ToolResult(content=f"Expert '{expert_name}' timed out after {timeout}s", is_error=True)

        # Add expert message to discussion
        if self.discussion and self.discussion.id and not result.is_error:
            msg = Message(
                entity_id=expert_entity["id"],
                entity_name=expert_entity["name"],
                content=result.content,
                role=MessageRole.PARTICIPANT,
                timestamp=time.time(),
            )
            self.discussion.messages.append(msg)
            self.db.add_message(
                self.discussion.id, msg.entity_id, msg.content,
                msg.role.value, turn_number=self.discussion.turn_number,
                timestamp=msg.timestamp,
            )
            self._notify()

        return result

    async def consult_expert(self, expert_name: str, query: str) -> dict:
        """Public method: consult an expert by name with a query."""
        ctx = ToolContext(
            caller_entity_id=0,
            discussion_id=self.discussion.id if self.discussion else 0,
        )
        result = await self._handle_consult_expert(
            {"expert_name": expert_name, "query": query}, ctx,
        )
        return {"content": result.content, "is_error": result.is_error}
