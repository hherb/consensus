"""Application state and API for the discussion system."""

import asyncio
import contextvars
import json
import logging
import time
from typing import Any, Optional, Callable

from .ai_client import AIClient
from .models import (
    Discussion, Entity, EntityType, Message, MessageRole, StoryboardEntry,
    resolve_api_key,
)
from .moderator import Moderator
from .database import Database
from . import app_entities, app_providers, app_discussion_setup, app_discussion_flow
from .config import get_db_path, save_api_key, remove_api_key, has_api_key
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
        self.memory_available = False
        self._init_builtin_tools()
        self._init_memory_tools()

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
                       args: list | None = None, env: dict | None = None) -> dict | None:
        """Register an MCP server and return its record."""
        server_id = self.db.add_mcp_server(name, description, command, args, env)
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

    async def test_mcp_connection(self, server_id: int) -> dict:
        """Test connectivity to an MCP server and list its tools."""
        from .mcp_client import MCPToolProvider
        server = self.db.get_mcp_server(server_id)
        if not server:
            return {"success": False, "error": "Server not found"}
        provider = MCPToolProvider(
            name=server["name"], command=server["command"],
            args=server["args"], env=server["env"],
        )
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
        from .mcp_client import MCPToolProvider
        server = self.db.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            return False
        provider = MCPToolProvider(
            name=server["name"], command=server["command"],
            args=server["args"], env=server["env"],
        )
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
        await app_discussion_flow.conclude_discussion(
            self.discussion, self.moderator, self.db, self.db.pricing,
        )
        self._notify()
        return self.get_state()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export without mutating current state."""
        disc = self.db.get_discussion(discussion_id)
        if not disc:
            return {"error": "Discussion not found"}

        members = self.db.get_discussion_members(discussion_id)
        messages = self.db.get_messages(discussion_id)
        storyboard = self.db.get_storyboard(discussion_id)

        entities = [Entity.from_db_row(m) for m in members]
        msgs = [Message.from_db_row(m) for m in messages]
        sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

        turn_order = [
            m["entity_id"] for m in members
            if m.get("turn_position") is not None
        ]

        status = disc["status"]
        d = Discussion(
            id=discussion_id,
            topic=disc["topic"],
            entities=entities,
            moderator_id=disc.get("moderator_id"),
            messages=msgs,
            storyboard=sb,
            turn_order=turn_order,
            is_active=status == "active",
            status=status,
        )
        return d.to_dict()

    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion, restoring full state including turn position."""
        disc = self.db.get_discussion(discussion_id)
        if not disc:
            return {"error": "Discussion not found"}

        members = self.db.get_discussion_members(discussion_id)
        messages = self.db.get_messages(discussion_id)
        storyboard = self.db.get_storyboard(discussion_id)

        entities = [Entity.from_db_row(m) for m in members]
        msgs = [Message.from_db_row(m) for m in messages]
        sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

        # Restore turn order from discussion_members.turn_position
        turn_order: list[int] = [
            m["entity_id"] for m in members
            if m.get("turn_position") is not None
        ]

        status = disc["status"]
        is_active = status == "active"

        # Recover turn state for resumable discussions
        current_turn_index = 0
        turn_number = 0
        if status in ("active", "paused") and turn_order and msgs:
            turn_number = self.db.get_max_turn_number(discussion_id)
            # Find the last participant message to determine next speaker
            last_participant = next(
                (m for m in reversed(msgs)
                 if m.role == MessageRole.PARTICIPANT),
                None,
            )
            if last_participant and last_participant.entity_id in turn_order:
                last_idx = turn_order.index(last_participant.entity_id)
                current_turn_index = (last_idx + 1) % len(turn_order)
            turn_number = max(turn_number, 1)

        # Restore member roles from DB
        member_roles = {
            m["entity_id"]: m.get("participant_role", "standard")
            for m in members
        }

        self.discussion = Discussion(
            id=discussion_id,
            topic=disc["topic"],
            entities=entities,
            moderator_id=disc.get("moderator_id"),
            messages=msgs,
            storyboard=sb,
            turn_order=turn_order,
            current_turn_index=current_turn_index,
            turn_number=turn_number,
            max_rounds=disc.get("max_rounds", 0),
            is_active=is_active,
            status=status,
            member_roles=member_roles,
        )
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
        )
        self._notify()
        return self.get_state()

    def delete_discussions(self, discussion_ids: list[int]) -> dict:
        """Soft-delete discussions by IDs."""
        count = self.db.soft_delete_discussions(discussion_ids)
        return {"deleted": count, "state": self.get_state()}

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        restored = self.db.restore_discussion(discussion_id)
        return {"restored": restored, "state": self.get_state()}

    def pause_discussion(self) -> dict:
        """Pause the current active discussion."""
        if not self.discussion.id or self.discussion.status != "active":
            return {"error": "Discussion is not active"}

        self.discussion.status = "paused"
        self.discussion.is_active = False
        self.db.update_discussion(self.discussion.id, status="paused")

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion paused --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion paused --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        if not self.discussion.id or self.discussion.status != "paused":
            return {"error": "Discussion is not paused"}

        self.discussion.status = "active"
        self.discussion.is_active = True
        self.db.update_discussion(self.discussion.id, status="active")

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion resumed --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion resumed --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion for continuation.

        Transitions the discussion to 'paused' so the user can manage
        participants before resuming with a new prompt.
        """
        if not self.discussion.id:
            return {"error": "No discussion loaded"}
        if self.discussion.status != "concluded":
            return {"error": "Discussion is not concluded"}

        self.discussion.status = "paused"
        self.discussion.is_active = False
        self.db.update_discussion(
            self.discussion.id, status="paused", ended_at=None,
        )

        # Restore turn state so the discussion can continue
        if self.discussion.turn_order:
            self.discussion.current_turn_index = 0
        self.discussion.turn_number = (
            self.db.get_max_turn_number(self.discussion.id) + 1
        )

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion reopened --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion reopened --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def reset(self) -> bool:
        """Reset to a clean state for a new discussion."""
        self.discussion = Discussion()
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
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
