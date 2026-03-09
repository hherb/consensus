"""Desktop launcher using pywebview with JS-Python bridge."""

import asyncio
import json
import logging
import os
import sys
import threading
from typing import Optional

from .app import ConsensusApp

logger = logging.getLogger(__name__)

# Desktop window configuration
WINDOW_TITLE = "Consensus - Discussion Moderator"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 900
WINDOW_MIN_HEIGHT = 600

# Timeout for async operations bridged from pywebview (seconds)
ASYNC_BRIDGE_TIMEOUT = 180


class DesktopBridge:
    """API exposed to JavaScript via pywebview's js_api.

    Each public method (no leading underscore) is callable from the
    browser via ``window.pywebview.api.<method_name>(...)``.  Async
    application methods are bridged through a background event loop.
    """

    def __init__(self, app: ConsensusApp) -> None:
        self.app = app
        self._window: Optional[object] = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        app.set_update_callback(self._push_state)
        app.on("tool_progress", self._push_progress)

    def _push_state(self, state: dict) -> None:
        """Push state updates to the frontend via evaluate_js."""
        if self._window:
            try:
                js = (
                    'if(typeof onStateUpdate==="function")'
                    f'onStateUpdate({json.dumps(state)})'
                )
                self._window.evaluate_js(js)
            except Exception:
                logger.debug("Failed to push state to webview", exc_info=True)

    def _push_progress(self, data: dict) -> None:
        """Forward tool progress events to the JS frontend."""
        if self._window is None:
            return
        try:
            payload = json.dumps(data)
            self._window.evaluate_js(f"if(window.onToolProgress) onToolProgress({payload})")
        except Exception:
            logger.debug("Failed to push progress event")

    def _run_async(self, coro: object) -> object:
        """Run an async coroutine on the background event loop and block for result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=ASYNC_BRIDGE_TIMEOUT)

    # -- State --
    def get_state(self) -> dict:
        """Return full application state."""
        return self.app.get_state()

    # -- Providers --
    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider."""
        return self.app.add_provider(name, base_url, api_key_env, api_key)

    def update_provider(self, provider_id: int, name: str = "",
                        base_url: str = "",
                        api_key_env: str = "",
                        api_key: str = "") -> bool:
        """Update an existing provider."""
        kwargs: dict[str, str] = {}
        if name:
            kwargs["name"] = name
        if base_url:
            kwargs["base_url"] = base_url
        if api_key_env is not None:
            kwargs["api_key_env"] = api_key_env
        return self.app.update_provider(provider_id, api_key=api_key, **kwargs)

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider by ID."""
        return self.app.delete_provider(provider_id)

    def fetch_models(self, provider_id: int) -> list:
        """Fetch available models from a provider's API."""
        return self._run_async(self.app.fetch_models(provider_id))

    # -- Entity profiles --
    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: int = 0, model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "", entity_id: int = 0) -> Optional[dict]:
        """Create or update a persistent entity profile."""
        return self.app.save_entity(
            name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt, entity_id,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete or deactivate an entity profile."""
        return self.app.delete_entity(entity_id)

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a previously deactivated entity profile."""
        return self.app.reactivate_entity(entity_id)

    def get_inactive_entities(self) -> list[dict]:
        """Return all inactive (soft-deleted) entity profiles."""
        return self.app.get_inactive_entities()

    # -- Prompts --
    def save_prompt(self, prompt_id: int, name: str, role: str,
                    target: str, task: str, content: str) -> Optional[dict]:
        """Create or update a prompt template."""
        return self.app.save_prompt(
            prompt_id, name, role, target, task, content,
        )

    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt template."""
        return self.app.delete_prompt(prompt_id)

    # -- Discussion setup --
    def add_to_discussion(self, entity_id: int, is_moderator: bool = False,
                          also_participant: bool = False,
                          participant_role: str = "standard") -> dict:
        """Add a saved entity to the current discussion."""
        return self.app.add_to_discussion(
            entity_id, is_moderator, also_participant, participant_role,
        )

    def remove_from_discussion(self, entity_id: int) -> bool:
        """Remove an entity from the discussion."""
        return self.app.remove_from_discussion(entity_id)

    def set_moderator(self, entity_id: int,
                      also_participant: bool = False) -> bool:
        """Designate a moderator for the discussion."""
        return self.app.set_moderator(entity_id, also_participant)

    def set_participant_role(self, entity_id: int,
                             participant_role: str = "standard") -> dict:
        """Set or change a participant's role."""
        return self.app.set_participant_role(entity_id, participant_role)

    def set_topic(self, topic: str) -> bool:
        """Set the discussion topic."""
        return self.app.set_topic(topic)

    # -- Discussion lifecycle --
    def start_discussion(self, moderator_participates: bool = False,
                         max_rounds: int = 0) -> dict:
        """Start the discussion."""
        return self.app.start_discussion(moderator_participates, max_rounds)

    def submit_human_message(self, entity_id: int, content: str) -> dict:
        """Submit a message from a human participant."""
        return self.app.submit_human_message(entity_id, content)

    def submit_moderator_message(self, content: str) -> dict:
        """Submit a message from the human moderator."""
        return self.app.submit_moderator_message(content)

    def generate_ai_turn(self) -> dict:
        """Generate the current AI speaker's contribution."""
        return self._run_async(self.app.generate_ai_turn())

    def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete the current turn with optional human moderator summary."""
        return self._run_async(self.app.complete_turn(moderator_summary))

    def reassign_turn(self, entity_id: int) -> dict:
        """Reassign the current turn to another participant."""
        return self.app.reassign_turn(entity_id)

    def mediate(self, context: str = "") -> dict:
        """Have the moderator intervene to mediate."""
        return self._run_async(self.app.mediate(context))

    def conclude(self) -> dict:
        """End the discussion and generate a conclusion."""
        return self._run_async(self.app.conclude_discussion())

    # -- Tools --
    def list_tools(self) -> list:
        """List all available tools from all providers."""
        return self._run_async(self.app.list_available_tools())

    def get_entity_tools(self, entity_id: int) -> list:
        """Get tool assignments for an entity."""
        return self.app.get_entity_tools(entity_id)

    def assign_tool(self, entity_id: int, tool_name: str,
                    access_mode: str = "private") -> bool:
        """Assign a tool to an entity."""
        return self.app.assign_tool_to_entity(entity_id, tool_name, access_mode)

    def remove_tool(self, entity_id: int, tool_name: str) -> bool:
        """Remove a tool assignment from an entity."""
        return self.app.remove_entity_tool(entity_id, tool_name)

    def set_tool_override(self, discussion_id: int, entity_id: int,
                          tool_name: str, enabled: bool) -> bool:
        """Set a per-discussion tool override."""
        return self.app.set_discussion_tool_override(
            discussion_id, entity_id, tool_name, enabled,
        )

    # -- MCP servers --
    def get_mcp_servers(self) -> list:
        """List all configured MCP servers."""
        return self.app.get_mcp_servers()

    def add_mcp_server(self, name, description, command, args=None, env=None) -> dict:
        """Add a new MCP server configuration."""
        return self.app.add_mcp_server(name, description, command, args, env)

    def update_mcp_server(self, server_id, **kwargs) -> bool:
        """Update an existing MCP server configuration."""
        return self.app.update_mcp_server(server_id, **kwargs)

    def delete_mcp_server(self, server_id) -> bool:
        """Delete an MCP server configuration."""
        return self._run_async(self.app.delete_mcp_server(server_id))

    def test_mcp_connection(self, server_id) -> dict:
        """Test connectivity to an MCP server."""
        return self._run_async(self.app.test_mcp_connection(server_id))

    # -- Experts --
    def save_expert_definition(self, entity_id, mcp_server_id, tool_name,
                               description="", default_arguments=None,
                               query_param_name="query", timeout_seconds=300) -> dict:
        """Save an expert definition linking an entity to an MCP tool."""
        return self.app.save_expert_definition(entity_id, mcp_server_id, tool_name,
                                                description, default_arguments,
                                                query_param_name, timeout_seconds)

    def get_expert_definitions(self) -> list:
        """List all expert definitions."""
        return self.app.get_expert_definitions()

    def consult_expert(self, expert_name, query) -> dict:
        """Consult an expert by name with a query."""
        return self._run_async(self.app.consult_expert(expert_name, query))

    # -- Memory config --
    def get_memory_config(self) -> dict:
        """Return memory configuration."""
        if not self.app.memory_available:
            return {"error": "Memory feature not available"}
        return self.app.db.get_memory_config()

    def save_memory_config(self, data: dict) -> dict:
        """Update memory configuration keys."""
        if not self.app.memory_available:
            return {"error": "Memory feature not available"}
        allowed_keys = {"embedding_backend", "embedding_model", "embedding_endpoint"}
        for key, value in data.items():
            if key in allowed_keys:
                self.app.db.set_memory_config(key, str(value))
        return {"ok": True}

    def test_memory_connection(self) -> dict:
        """Test the embedding connection."""
        if not self.app.memory_available:
            return {"ok": False, "message": "Memory feature not available"}
        try:
            from .tools_memory import EmbeddingClient
            client = EmbeddingClient(self.app.db)
            ok, message = self._run_async(client.test_connection())
            return {"ok": ok, "message": message}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # -- Export --
    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export without mutating current state."""
        return self.app.get_export_data(discussion_id)

    def save_file(self, content: str, filename: str, file_types: str = "") -> bool:
        """Show a native save dialog and write content to the chosen path."""
        import webview
        if not self._window:
            return False
        file_filter = file_types or "All files (*.*)"
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=(file_filter,),
        )
        if not result:
            return False
        path = result if isinstance(result, str) else result[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    def pause_discussion(self) -> dict:
        """Pause the active discussion."""
        return self.app.pause_discussion()

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        return self.app.resume_discussion()

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion for continuation."""
        return self.app.reopen_discussion()

    # -- History --
    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion for review."""
        return self.app.load_discussion(discussion_id)

    def delete_discussions(self, discussion_ids: list) -> dict:
        """Soft-delete discussions by IDs."""
        return self.app.delete_discussions([int(i) for i in discussion_ids])

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        return self.app.restore_discussion(int(discussion_id))

    def reset(self) -> bool:
        """Reset to a clean state."""
        return self.app.reset()

    # -- Evaluation --
    def open_evaluation(self) -> dict:
        """Start the evaluation server (if needed) and open in browser."""
        return self._run_async(self._open_evaluation_async())

    async def _open_evaluation_async(self) -> dict:
        if not hasattr(self, '_eval_server_url'):
            try:
                from evaluation.eval_db import EvalDatabase
                from evaluation.eval_routes import register_eval_routes
                from aiohttp import web

                eval_db = EvalDatabase()
                eval_app = web.Application()
                register_eval_routes(eval_app, eval_db)

                runner = web.AppRunner(eval_app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                # Get the actual port assigned
                port = site._server.sockets[0].getsockname()[1]
                self._eval_server_url = f"http://127.0.0.1:{port}/eval/"
                self._eval_runner = runner
                logger.info("Evaluation server started at %s",
                            self._eval_server_url)
            except Exception:
                logger.exception("Failed to start evaluation server")
                return {"error": "Evaluation module not available"}

        import webbrowser
        webbrowser.open(self._eval_server_url)
        return {"url": self._eval_server_url}


def launch_desktop(debug: bool = False) -> None:
    """Launch the desktop application using pywebview."""
    import webview
    from .config import load_env
    load_env()

    app = ConsensusApp()
    bridge = DesktopBridge(app)

    pkg_dir = os.path.dirname(__file__)
    static_dir = os.path.join(pkg_dir, "static")
    html_path = os.path.join(static_dir, "index.html")

    # App icon — resolve from assets/ relative to repo root
    icon_path = os.path.normpath(
        os.path.join(pkg_dir, "..", "assets", "consensus_icon.png"))
    if not os.path.exists(icon_path):
        icon_path = None

    # macOS: set dock icon via AppKit (pywebview doesn't support this natively)
    if icon_path and sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSImage
            ns_app = NSApplication.sharedApplication()
            ns_app.setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(icon_path))
        except Exception:
            logger.debug("Could not set macOS dock icon", exc_info=True)

    # Windows: set taskbar icon via ctypes
    if icon_path and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "consensus.discussion.moderator")
        except Exception:
            logger.debug("Could not set Windows app ID", exc_info=True)

    window = webview.create_window(
        WINDOW_TITLE,
        html_path,
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
    )
    bridge._window = window

    # icon param in webview.start() works on Linux (GTK/QT)
    start_kwargs = {"debug": debug}
    if icon_path and sys.platform.startswith("linux"):
        start_kwargs["icon"] = icon_path
    webview.start(**start_kwargs)
