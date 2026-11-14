"""Application state and API for the discussion system."""

import asyncio
import logging
from typing import Any, Optional, Callable

from .models import Discussion, EntityType, resolve_api_key
from .moderator import Moderator
from .database import Database
from .app_api import (
    CatalogMixin, DiscussionMixin, DocumentsMixin, HistoryMixin,
    ImagesMixin, MCPExpertsMixin, ToolAssignmentsMixin,
)
from .app_api.request_keys import _request_api_keys_var
from .config import get_db_path
from .tools import PythonToolProvider, ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)


class ConsensusApp(
    MCPExpertsMixin,
    CatalogMixin,
    DiscussionMixin,
    HistoryMixin,
    ToolAssignmentsMixin,
    DocumentsMixin,
    ImagesMixin,
):
    """Main application controller backed by SQLite.

    Owns all state; the public API is grouped into the ``app_api`` mixins
    (issue #61). This class keeps construction, tool registration, BYOK key
    resolution, the event emitter and ``get_state``.
    """

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
            progress_callback=self._emit_tool_progress,
        )
        self._on_update: Optional[Callable] = None
        self._event_listeners: dict[str, list[Callable]] = {}
        self._mcp_providers: dict[int, Any] = {}
        self._pending_user_inputs: dict[str, tuple[asyncio.Future, dict]] = {}
        self.memory_available = False
        self.documents_available = False
        self.images_available = False
        # Cache embedding availability (static for session lifetime)
        try:
            mem_cfg = self.db.get_memory_config()
            self._embedding_available = bool(mem_cfg.get("embedding_endpoint"))
        except Exception:
            self._embedding_available = False
        self._init_builtin_tools()
        self._init_interactive_tools()
        self._init_memory_tools()
        self._init_document_tools()
        self._init_image_tools()
        self._init_python_tools()
        self._load_mcp_config()

    def _refresh_pricing_if_needed(self) -> None:
        """Refresh pricing cache in the background on startup if stale."""
        try:
            if self.db.pricing._needs_refresh():
                self.db.pricing.refresh_in_background()
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

        list_experts_def = ToolDefinition(
            name="list_available_experts",
            description="List all specialist experts available for consultation. Call this before consult_expert to discover which experts exist.",
            parameters={"type": "object", "properties": {}},
        )
        expert_provider.register(list_experts_def, self._handle_list_experts)
        self.tool_registry.register_provider(expert_provider)

    def _init_interactive_tools(self) -> None:
        """Register interactive tool providers (ask_user)."""
        from .tools_ask_user import create_ask_user_provider
        provider = create_ask_user_provider(self)
        self.tool_registry.register_provider(provider)
        self.db.add_tool_provider("interactive", "python")

    def _init_memory_tools(self) -> None:
        """Register institutional memory tool provider (needs sqlite-vec; a default dependency)."""
        try:
            import sqlite_vec  # noqa: F401
            from .tools_memory import create_memory_provider
            provider = create_memory_provider(self.db)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("memory", "python")
            self.memory_available = True
            logger.debug("Institutional memory tools registered")
        except ImportError as e:
            # A default install always has the deps, so this is a broken
            # install or a broken import inside the package: say so loudly.
            logger.warning("Memory tools failed to load: %s", e, exc_info=e)

    def _init_document_tools(self) -> None:
        """Register document RAG tool provider (needs sqlite-vec; a default dependency)."""
        try:
            import sqlite_vec  # noqa: F401
            from .tools_document import create_document_provider
            provider = create_document_provider(self.db, app=self)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("documents", "python")
            self.documents_available = True
            logger.debug("Document RAG tools registered")
        except ImportError as e:
            logger.warning("Document tools failed to load: %s", e, exc_info=e)

    def _init_image_tools(self) -> None:
        """Register image tool provider."""
        try:
            from .tools_image import create_image_provider
            provider = create_image_provider(self.db, app=self)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("images", "python")
            self.images_available = True
            logger.debug("Image tools registered")
        except Exception:
            # Pillow is imported lazily per call, so nothing optional can
            # fail here: a load failure is a real fault (e.g. a broken
            # facade after the #61 split), not INFO.
            self.images_available = False
            logger.warning("Image tools failed to load", exc_info=True)

    def _init_python_tools(self) -> None:
        """Register Python code execution tool provider."""
        try:
            from .tools_python import create_python_provider
            provider = create_python_provider(app=self)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("python_exec", "python")
            logger.debug("Python execution tools registered")
        except Exception:
            # No optional deps here, so any failure is a real fault.
            logger.warning("Python execution tools failed to load", exc_info=True)

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

    def _emit_tool_progress(self, data: dict) -> None:
        """Forward a Moderator tool-loop progress dict as a tool_progress
        event (the channel the desktop bridge and web SSE already relay)."""
        self.emit("tool_progress", data)

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
        # Per-member context strategy configs (for the roster UI)
        if self.discussion.id:
            members = self.db.get_discussion_members(self.discussion.id)
            state["member_context_configs"] = {
                str(m["entity_id"]): {
                    "strategy": m.get("context_strategy") if m.get("context_strategy") is not None else "sliding_window",
                    "window_size": m.get("context_window_size") if m.get("context_window_size") is not None else 20,
                }
                for m in members
            }
        else:
            # During setup, use in-memory overrides
            state["member_context_configs"] = {
                str(eid): cfg
                for eid, cfg in self.discussion.member_context_configs.items()
            }
        # Whether embedding service is configured (for semantic strategy availability)
        state["embedding_available"] = self._embedding_available
        # Whether the active phase tracks evidence (#28) — lets the frontend
        # gate the "Attach evidence" button to phases where a citation marker
        # is actually classified, rather than showing it on every human turn.
        from .methods import get_active_method
        method = get_active_method(self.discussion)
        phase = method.current_phase(self.discussion) if method else None
        state["track_evidence_phase"] = bool(
            phase is not None and phase.track_evidence)
        # Structured-phase input spec (#57): when the current speaker is a
        # human participant (not the moderator) in a phase that declares an
        # output tool, expose the tool schema so the frontend renders a form
        # instead of forcing raw JSON into the plain textarea.
        from .structured_input import build_input_spec
        current = self.discussion.current_speaker
        input_spec = None
        if (current is not None
                and current.entity_type == EntityType.HUMAN
                and current.id != self.discussion.moderator_id):
            input_spec = build_input_spec(method, current, self.discussion)
        state["current_input_spec"] = input_spec
        # Same-model panel advisory (#29): for methods that assume
        # independent estimators, warn when one model dominates the panel.
        state["panel_advisory"] = None
        if method is not None and method.assumes_independent_panel:
            from .methods.panel_diversity import (
                analyze_panel_diversity, estimator_models, format_setup_warning,
            )
            report = analyze_panel_diversity(estimator_models(self.discussion))
            msg = format_setup_warning(report)
            if msg:
                state["panel_advisory"] = {"level": "warning", "message": msg}
        # Pending blocked method switch (triage handoff, spec
        # 2026-07-17) — exposed so the recovery dialog reappears after
        # a reload/reconnect, like pending_user_input below.
        pending_switch = None
        if (self.discussion.discussion_method == "triage"
                and self.discussion.status != "concluded"):
            pending_switch = self.discussion.method_state.get(
                "_pending_method_switch")
        state["pending_method_switch"] = pending_switch
        # Expose pending user-input request for reconnection scenarios
        if self._pending_user_inputs:
            _rid, (_fut, _data) = next(iter(self._pending_user_inputs.items()))
            state["pending_user_input"] = _data
        else:
            state["pending_user_input"] = None
        return state
