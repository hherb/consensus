"""Application state and API for the discussion system."""

import asyncio
import contextvars
import logging
import time
from typing import Any, Optional, Callable

from .models import (
    Discussion, Message, MessageRole,
    resolve_api_key,
)
from .moderator import Moderator
from .database import Database
from . import (
    app_discussion_flow, app_discussion_setup, app_discussion_state,
    app_entities, app_providers,
)
from .config import get_db_path
from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Per-request BYOK API keys, isolated via contextvars (no cross-request leakage)
_request_api_keys_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "_request_api_keys_var", default={},
)


class ConsensusApp:
    """Main application controller backed by SQLite."""

    def __init__(self, db_path: str = "") -> None:
        self.db = Database(db_path or get_db_path())
        self._refresh_pricing_if_needed()
        self.db.purge_deleted_discussions()
        self.discussion = Discussion()
        self.tool_registry = ToolRegistry(db=self.db)
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
        )
        self._on_update: Optional[Callable] = None
        self._event_listeners: dict[str, list[Callable]] = {}
        self._mcp_providers: dict[int, Any] = {}
        self._pending_user_inputs: dict[str, tuple[asyncio.Future, dict]] = {}
        self.memory_available = False
        self.documents_available = False
        self.images_available = False
        self._init_builtin_tools()
        self._init_interactive_tools()
        self._init_memory_tools()
        self._init_document_tools()
        self._init_image_tools()
        self._load_mcp_config()

    def _refresh_pricing_if_needed(self) -> None:
        """Refresh pricing cache on startup if stale or missing models."""
        try:
            if self.db.pricing._needs_refresh():
                self.db.pricing.refresh()
        except Exception:
            logger.warning("Failed to refresh pricing on startup", exc_info=True)

    def _init_builtin_tools(self) -> None:
        """Register built-in tool providers."""
        try:
            from .tools_builtin import create_web_search_provider
            provider = create_web_search_provider()
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("builtin", "python")
        except ImportError:
            logger.debug("Built-in tools not available")

        # Register consult_expert meta-tool
        expert_provider = PythonToolProvider(name="experts")
        expert_tool_def = ToolDefinition(
            name="consult_expert",
            description="Consult a specialist expert for authoritative analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "expert_name": {"type": "string", "description": "Name of the expert to consult"},
                    "query": {"type": "string", "description": "The question or claim to present"},
                },
                "required": ["expert_name", "query"],
            },
        )
        expert_provider.register(expert_tool_def, self._handle_consult_expert)
        self.tool_registry.register_provider(expert_provider)

    def _init_interactive_tools(self) -> None:
        """Register interactive tool providers (ask_user)."""
        from .tools_ask_user import create_ask_user_provider
        provider = create_ask_user_provider(self)
        self.tool_registry.register_provider(provider)
        self.db.add_tool_provider("interactive", "python")

    def _init_memory_tools(self) -> None:
        """Register institutional memory tool provider (requires [memory] extras)."""
        try:
            import sqlite_vec  # noqa: F401
            from .tools_memory import create_memory_provider
            provider = create_memory_provider(self.db)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("memory", "python")
            self.memory_available = True
            logger.debug("Institutional memory tools registered")
        except ImportError as e:
            logger.info("Memory tools not available: %s", e)

    def _init_document_tools(self) -> None:
        """Register document RAG tool provider (requires [memory] extras)."""
        try:
            import sqlite_vec  # noqa: F401
            from .tools_document import create_document_provider
            provider = create_document_provider(self.db, app=self)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("documents", "python")
            self.documents_available = True
            logger.debug("Document RAG tools registered")
        except ImportError as e:
            logger.info("Document tools not available: %s", e)

    def _init_image_tools(self) -> None:
        """Register image tool provider."""
        try:
            from .tools_image import create_image_provider
            provider = create_image_provider(self.db, app=self)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("images", "python")
            self.images_available = True
            logger.debug("Image tools registered")
        except ImportError as e:
            self.images_available = False
            logger.info("Image tools not available: %s", e)

    def _load_mcp_config(self) -> None:
        """Load MCP server definitions from config files on startup."""
        try:
            from .mcp_config import load_and_merge_config
            result = load_and_merge_config(self.db)
            if result["added"] or result["updated"]:
                logger.info("MCP config: %d added, %d updated",
                            result["added"], result["updated"])
        except Exception:
            logger.warning("Failed to load MCP config", exc_info=True)

    @staticmethod
    def set_request_api_keys(keys: dict[str, str]) -> None:
        """Set per-request API keys (BYOK) via contextvars. Called by the web server."""
        _request_api_keys_var.set(keys)

    @staticmethod
    def clear_request_api_keys() -> None:
        """Clear per-request API keys after the request is handled."""
        _request_api_keys_var.set({})

    def _resolve_key_for_moderator(self, provider_id: int,
                                   env_var: str) -> str:
        """Key resolver callback for the Moderator's AI clients."""
        # Look up env_var from DB if not provided
        if not env_var and provider_id:
            provider = self.db.get_provider(provider_id)
            if provider:
                env_var = provider.get("api_key_env") or ""
        return self.resolve_provider_api_key(provider_id, env_var)

    def resolve_provider_api_key(self, provider_id: int,
                                 env_var: str) -> str:
        """Resolve API key for a provider: BYOK first, then env var.

        Order of precedence:
        1. Per-request BYOK key (from browser)
        2. Environment variable (from server config / .env file)
        """
        # Check BYOK keys first (from contextvars, request-scoped)
        byok_key = _request_api_keys_var.get({}).get(str(provider_id), "")
        if byok_key:
            return byok_key
        # Fall back to env-based resolution (original behavior)
        return resolve_api_key(env_var or "")

    def set_update_callback(self, callback: Callable) -> None:
        """Register a callback invoked whenever state changes."""
        self._on_update = callback

    def _notify(self) -> None:
        """Push current state to the registered update callback."""
        if self._on_update:
            self._on_update(self.get_state())

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Close all MCP provider connections. Call on app exit."""
        for provider in self._mcp_providers.values():
            try:
                await provider.close()
            except Exception:
                logger.debug("Error closing MCP provider", exc_info=True)
        self._mcp_providers.clear()

    # ------------------------------------------------------------------
    # Event emitter
    # ------------------------------------------------------------------

    def on(self, event_type: str, callback: Callable) -> None:
        """Subscribe to an event type."""
        self._event_listeners.setdefault(event_type, []).append(callback)

    def off(self, event_type: str, callback: Callable) -> None:
        """Unsubscribe from an event type."""
        listeners = self._event_listeners.get(event_type, [])
        if callback in listeners:
            listeners.remove(callback)

    def emit(self, event_type: str, data: dict) -> None:
        """Emit an event to all subscribers."""
        for cb in self._event_listeners.get(event_type, []):
            try:
                cb(data)
            except Exception:
                logger.exception("Event listener error for %s", event_type)

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
            from .mcp_http_client import MCPHTTPToolProvider
            return MCPHTTPToolProvider(
                name=server["name"],
                url=server.get("url", ""),
                headers=server.get("headers", {}),
            )
        from .mcp_client import MCPToolProvider
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
                return {"success": False, "error": "Failed to connect"}
            tools = await provider.list_tools()
            tool_info = [{"name": t.name, "description": t.description} for t in tools]
            return {"success": True, "tools": tool_info}
        except Exception as e:
            return {"success": False, "error": str(e)}
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
            self.db.save_message(self.discussion.id, msg)
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

    # ------------------------------------------------------------------
    # State for the frontend
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return the complete application state for the frontend."""
        state = self.discussion.to_dict()
        state["providers"] = self.get_providers()
        state["saved_entities"] = self.db.get_entities()
        state["prompts"] = self.db.get_prompts()
        state["discussions_history"] = self.db.get_discussions()
        state["tool_providers"] = self.db.get_tool_providers()
        state["mcp_servers"] = self.db.get_mcp_servers()
        state["experts"] = self.db.get_expert_definitions()
        # Include discussion images (from DB if active, from pending list during setup)
        state["discussion_images"] = self.get_discussion_images()
        # Expose pending user-input request for reconnection scenarios
        if self._pending_user_inputs:
            _rid, (_fut, _data) = next(iter(self._pending_user_inputs.items()))
            state["pending_user_input"] = _data
        else:
            state["pending_user_input"] = None
        return state

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def _provider_for_frontend(self, p: Optional[dict]) -> Optional[dict]:
        """Redact secrets before sending provider data to the frontend."""
        return app_providers.provider_for_frontend(p, _request_api_keys_var.get({}))

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider and return its data."""
        return app_providers.add_provider(
            self.db, name, base_url, api_key_env, api_key,
            _request_api_keys_var.get({}),
        )

    def update_provider(self, provider_id: int,
                        api_key: str = "", **kwargs: object) -> bool:
        """Update an existing provider's fields."""
        return app_providers.update_provider(
            self.db, provider_id, api_key, **kwargs,
        )

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider by ID."""
        return app_providers.delete_provider(self.db, provider_id)

    def get_providers(self) -> list[dict]:
        """Return all configured providers (keys redacted)."""
        return app_providers.get_providers(self.db, _request_api_keys_var.get({}))

    async def fetch_models(self, provider_id: int) -> list[str]:
        """Fetch available models from a provider's API."""
        return await app_providers.fetch_models(
            self.db, provider_id, self.resolve_provider_api_key,
        )

    # ------------------------------------------------------------------
    # Entity profile management (persistent)
    # ------------------------------------------------------------------

    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: int = 0, model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "",
                    entity_id: int = 0) -> Optional[dict]:
        """Create or update a persistent entity profile."""
        return app_entities.save_entity(
            self.db, name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt, entity_id,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete or deactivate an entity profile by ID."""
        return app_entities.delete_entity(self.db, entity_id)

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a previously deactivated entity profile."""
        return app_entities.reactivate_entity(self.db, entity_id)

    def get_entities(self) -> list[dict]:
        """Return all saved active entity profiles."""
        return app_entities.get_entities(self.db)

    def get_inactive_entities(self) -> list[dict]:
        """Return all inactive (soft-deleted) entity profiles."""
        return app_entities.get_inactive_entities(self.db)

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    def save_prompt(self, prompt_id: int, name: str, role: str,
                    target: str, task: str, content: str) -> Optional[dict]:
        """Create or update a prompt template."""
        pid = self.db.save_prompt(
            prompt_id or None, name, role, target, task, content,
        )
        return self.db.get_prompt(pid)

    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt template by ID."""
        self.db.delete_prompt(prompt_id)
        return True

    def get_prompts(self) -> list[dict]:
        """Return all prompt templates."""
        return self.db.get_prompts()

    # ------------------------------------------------------------------
    # Discussion setup
    # ------------------------------------------------------------------

    def add_to_discussion(self, entity_id: int,
                          is_moderator: bool = False,
                          also_participant: bool = False,
                          participant_role: str = "standard") -> dict:
        """Add a saved entity to the current discussion."""
        result = app_discussion_setup.add_to_discussion(
            self.discussion, self.db, entity_id,
            is_moderator, also_participant, participant_role,
        )
        self._notify()
        return result

    def remove_from_discussion(self, entity_id: int) -> dict | bool:
        """Remove an entity from the current discussion."""
        result = app_discussion_setup.remove_from_discussion(
            self.discussion, self.db, entity_id,
        )
        self._notify()
        return result

    def set_moderator(self, entity_id: int,
                      also_participant: bool = False) -> bool:
        """Designate an entity as the moderator."""
        result = app_discussion_setup.set_moderator(self.discussion, entity_id, also_participant)
        if result:
            self._notify()
        return result

    def set_topic(self, topic: str) -> bool:
        """Set the discussion topic."""
        result = app_discussion_setup.set_topic(self.discussion, topic)
        self._notify()
        return result

    def set_participant_role(self, entity_id: int,
                             participant_role: str = "standard") -> dict:
        """Set or change a participant's role (e.g. devils_advocate)."""
        result = app_discussion_setup.set_participant_role(
            self.discussion, self.db, entity_id, participant_role,
        )
        self._notify()
        return result

    def _auto_assign_da_tools(self, entity_id: int) -> None:
        """Assign web search and memory tools to a devil's advocate entity."""
        app_discussion_setup.auto_assign_da_tools(self.db, entity_id)

    def _reorder_da_in_turn_order(self) -> None:
        """Ensure devil's advocate entity is last in turn order."""
        app_discussion_setup.reorder_da_in_turn_order(self.discussion)

    def set_discussion_method(self, method_name: str) -> dict:
        """Set the discussion method (e.g. 'ach', 'belief_diffusion').

        Must be called before starting the discussion.
        """
        try:
            result = app_discussion_setup.set_discussion_method(
                self.discussion, method_name,
            )
        except ValueError as e:
            return {"error": str(e)}
        self._notify()
        return result

    def list_discussion_methods(self) -> list[dict]:
        """Return metadata for all available discussion methods."""
        from .methods import list_methods
        return list_methods()

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    def start_discussion(self, moderator_participates: bool = False,
                         max_rounds: int = 0) -> dict:
        """Start a new discussion with the configured entities and topic."""
        result = app_discussion_setup.start_discussion(
            self.discussion, self.db, self.moderator,
            moderator_participates, max_rounds,
        )
        self._notify()
        if "error" in result:
            return result
        return self.get_state()

    def submit_human_message(self, entity_id: int, content: str) -> dict:
        """Submit a message from a human participant."""
        result = app_discussion_flow.submit_human_message(
            self.discussion, self.db, entity_id, content,
        )
        self._notify()
        return result

    def submit_moderator_message(self, content: str) -> dict:
        """Submit a message from the human moderator."""
        result = app_discussion_flow.submit_moderator_message(
            self.discussion, self.db, content,
        )
        self._notify()
        return result

    async def generate_ai_turn(self) -> dict:
        """Generate an AI participant's contribution for the current turn."""
        result = await app_discussion_flow.generate_ai_turn(
            self.discussion, self.moderator, self.db, self.db.pricing,
        )
        self._notify()
        return result

    async def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete the current turn: generate or accept summary, advance turn order."""
        result = await app_discussion_flow.complete_turn(
            self.discussion, self.moderator, self.db, self.db.pricing,
            self.get_state, moderator_summary,
        )
        self._notify()
        return result

    def reassign_turn(self, entity_id: int) -> dict:
        """Reassign the current turn to a different participant."""
        result = app_discussion_flow.reassign_turn(self.moderator, entity_id)
        if "error" not in result:
            self._notify()
            result["state"] = self.get_state()
        return result

    async def mediate(self, context: str = "") -> dict:
        """Have the moderator intervene to mediate a conflict."""
        result = await app_discussion_flow.mediate(
            self.discussion, self.moderator, self.db, self.db.pricing, context,
        )
        self._notify()
        return result

    async def conclude_discussion(self) -> dict:
        """End the discussion, generating a final synthesis if the moderator is AI."""
        self._cancel_pending_user_inputs()
        await app_discussion_flow.conclude_discussion(
            self.discussion, self.moderator, self.db, self.db.pricing,
        )
        self._notify()
        return self.get_state()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export."""
        return app_discussion_state.get_export_data(self.db, discussion_id)

    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion."""
        result = app_discussion_state.load_discussion(
            self.db, discussion_id,
            self._resolve_key_for_moderator, self.tool_registry,
        )
        if isinstance(result, dict):
            return result  # error
        self.discussion, self.moderator = result
        self._notify()
        return self.get_state()

    def delete_discussions(self, discussion_ids: list[int]) -> dict:
        """Soft-delete discussions."""
        result = app_discussion_state.delete_discussions(self.db, discussion_ids)
        result["state"] = self.get_state()
        return result

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        result = app_discussion_state.restore_discussion(self.db, discussion_id)
        result["state"] = self.get_state()
        return result

    def pause_discussion(self) -> dict:
        """Pause the current active discussion."""
        self._cancel_pending_user_inputs()
        result = app_discussion_state.pause_discussion(self.discussion, self.db)
        if "error" not in result:
            self._notify()
        return result

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        result = app_discussion_state.resume_discussion(self.discussion, self.db)
        if "error" not in result:
            self._notify()
        return result

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion."""
        result = app_discussion_state.reopen_discussion(self.discussion, self.db)
        if "error" not in result:
            self._notify()
        return result

    # ------------------------------------------------------------------
    # Interactive user input (ask_user tool)
    # ------------------------------------------------------------------

    def submit_user_input(self, request_id: str, content: str) -> dict:
        """Resolve a pending ask_user request with the user's response."""
        entry = self._pending_user_inputs.get(request_id)
        if not entry:
            return {"error": "No pending input request with that ID"}
        future, _data = entry
        if future.done():
            return {"error": "Request already resolved"}
        future.set_result(content)
        return {"ok": True}

    def cancel_user_input(self, request_id: str) -> dict:
        """Cancel a pending ask_user request."""
        entry = self._pending_user_inputs.pop(request_id, None)
        if not entry:
            return {"error": "No pending input request with that ID"}
        future, _data = entry
        if not future.done():
            future.cancel()
        return {"ok": True}

    def _cancel_pending_user_inputs(self) -> None:
        """Cancel all pending user-input futures (e.g. on pause/conclude)."""
        for request_id, (future, _data) in list(self._pending_user_inputs.items()):
            if not future.done():
                future.cancel()
        self._pending_user_inputs.clear()

    def reset(self) -> bool:
        """Reset to a clean state."""
        self.discussion, self.moderator = app_discussion_state.reset_discussion(
            self.db, self._resolve_key_for_moderator, self.tool_registry,
        )
        self._notify()
        return True

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    async def list_available_tools(self) -> list[dict]:
        """Return all tools from all registered providers."""
        tools = await self.tool_registry.list_all_tools()
        return [
            {"name": t.name, "description": t.description,
             "parameters": t.parameters, "provider": t.provider_name}
            for t in tools
        ]

    def get_entity_tools(self, entity_id: int) -> list[dict]:
        """Return tool assignments for an entity."""
        return self.db.get_entity_tools(entity_id)

    def assign_tool_to_entity(self, entity_id: int, tool_name: str,
                               access_mode: str = "private") -> bool:
        """Assign a tool to an entity with the specified access mode."""
        self.db.add_entity_tool(entity_id, tool_name, access_mode)
        return True

    def remove_entity_tool(self, entity_id: int, tool_name: str) -> bool:
        """Remove a tool assignment from an entity."""
        self.db.remove_entity_tool(entity_id, tool_name)
        return True

    def set_discussion_tool_override(self, discussion_id: int, entity_id: int,
                                      tool_name: str, enabled: bool) -> bool:
        """Set a per-discussion tool override."""
        self.db.set_discussion_tool_override(
            discussion_id, entity_id, tool_name, enabled,
        )
        return True

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def add_document(self, filename: str, content_bytes: bytes,
                           mime_type: str, discussion_id: int = 0,
                           source_url: str = "",
                           title: str = "") -> dict:
        """Add a document via file upload or URL fetch."""
        if not self.documents_available:
            return {"error": "Document tools not available (missing sqlite-vec)"}

        from .tools_document import ingest_document
        from .tools_memory import EmbeddingClient

        embed_client = EmbeddingClient(self.db)

        # Create a minimal context for summary generation
        context = ToolContext(
            caller_entity_id=0,
            discussion_id=discussion_id or (self.discussion.id if self.discussion else 0),
        )

        result = await ingest_document(
            app=self, db=self.db, embed_client=embed_client,
            content_bytes=content_bytes,
            filename=filename,
            mime_type=mime_type,
            discussion_id=discussion_id or (self.discussion.id if self.discussion else 0),
            source_url=source_url or None,
            title=title or None,
            source_type="url" if source_url else "upload",
            context=context,
        )
        return result

    async def add_document_from_url(self, url: str, discussion_id: int = 0,
                                     title: str = "") -> dict:
        """Add a document by fetching from a URL."""
        if not self.documents_available:
            return {"error": "Document tools not available (missing sqlite-vec)"}

        from .tools_document import fetch_url_content
        try:
            content_bytes, filename, mime_type = await fetch_url_content(url)
        except Exception as e:
            return {"error": f"Failed to fetch URL: {e}"}

        return await self.add_document(
            filename=filename, content_bytes=content_bytes,
            mime_type=mime_type, discussion_id=discussion_id,
            source_url=url, title=title,
        )

    def get_discussion_documents(self, discussion_id: int = 0) -> list[dict]:
        """Return documents attached to a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        return self.db.get_discussion_documents(disc_id)

    def remove_document(self, document_id: int, discussion_id: int = 0) -> dict:
        """Remove a document from a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        self.db.remove_discussion_document(disc_id, document_id)
        return {"success": True}

    def delete_document(self, document_id: int) -> dict:
        """Permanently delete a document and all its data."""
        deleted = self.db.delete_document(document_id)
        return {"success": deleted}

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    async def add_image(self, filename: str, content_bytes: bytes,
                        mime_type: str, discussion_id: int = 0,
                        source_url: str = "",
                        title: str = "") -> dict:
        """Add an image via file upload."""
        from .tools_image import ingest_image

        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        result = await ingest_image(
            db=self.db,
            content_bytes=content_bytes,
            filename=filename,
            mime_type=mime_type,
            discussion_id=disc_id,
            source_url=source_url or None,
            title=title or None,
            source_type="url" if source_url else "upload",
        )
        # Track images added before discussion has a DB ID
        if not disc_id and "image_id" in result and self.discussion:
            self.discussion.pending_image_ids.append(result["image_id"])
        return result

    async def add_image_from_url(self, url: str, discussion_id: int = 0,
                                  title: str = "") -> dict:
        """Add an image by fetching from a URL."""
        from .tools_image import fetch_image_from_url
        try:
            content_bytes, filename, mime_type = await fetch_image_from_url(url)
        except Exception as e:
            return {"error": f"Failed to fetch image: {e}"}

        return await self.add_image(
            filename=filename, content_bytes=content_bytes,
            mime_type=mime_type, discussion_id=discussion_id,
            source_url=url, title=title,
        )

    def get_discussion_images(self, discussion_id: int = 0) -> list[dict]:
        """Return images attached to a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        if disc_id:
            return self.db.get_discussion_images(disc_id)
        # During setup (no discussion ID yet), return pending images
        if self.discussion and self.discussion.pending_image_ids:
            return [
                img for img_id in self.discussion.pending_image_ids
                if (img := self.db.get_image(img_id))
            ]
        return []

    def remove_discussion_image(self, image_id: int,
                                 discussion_id: int = 0) -> dict:
        """Remove an image from a discussion (keeps in library)."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        if disc_id:
            self.db.remove_discussion_image(disc_id, image_id)
        elif self.discussion and image_id in self.discussion.pending_image_ids:
            self.discussion.pending_image_ids.remove(image_id)
        return {"success": True}

    def get_image_data(self, image_id: int) -> Optional[tuple[bytes, str, str]]:
        """Return (bytes, mime_type, filename) for serving an image."""
        image = self.db.get_image(image_id)
        if not image:
            return None
        from .tools_image import load_image_file
        try:
            data = load_image_file(image["storage_path"])
            return data, image["mime_type"], image["original_filename"]
        except FileNotFoundError:
            return None

    def delete_image(self, image_id: int) -> dict:
        """Permanently delete an image and its file."""
        image = self.db.get_image(image_id)
        if image:
            from .tools_image import delete_image_file
            delete_image_file(image["storage_path"])
        deleted = self.db.delete_image(image_id)
        return {"success": deleted}
