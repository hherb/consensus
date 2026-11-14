"""Characterization tests for ``ConsensusApp`` paths the suite did not reach.

Written before the issue #61 split of ``consensus/app.py`` into mixins, so
the move is verified by behaviour rather than by a green suite that never
executed the moved lines (``app.py`` was at 66% coverage). Real ``Database``
throughout; fakes only at the network edges (MCP providers, URL fetchers).
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from consensus.app import ConsensusApp
from consensus.http_fetch import FetchRejected
from consensus.tools import ToolContext, ToolDefinition, ToolResult
from consensus.tools_document.errors import DocumentParseError

from .image_helpers import make_image


# The ``app`` and ``images_dir`` fixtures live in conftest.py.


class FakeMCPProvider:
    """Stands in for an MCP client at the network edge."""

    def __init__(self, name: str = "fake", connects: bool = True,
                 tools: list | None = None, raises: Exception | None = None,
                 result: ToolResult | None = None, delay: float = 0.0,
                 last_error: str = "") -> None:
        self.name = name
        self.connects = connects
        self.last_error = last_error
        self.tools = tools or []
        self.raises = raises
        self.result = result or ToolResult(content="expert says hi")
        self.delay = delay
        self.closed = False
        self.executed: list[tuple[str, dict]] = []

    async def connect(self) -> bool:
        return self.connects

    async def list_tools(self) -> list:
        if self.raises:
            raise self.raises
        return self.tools

    async def execute(self, tool_name, args, context, progress_callback=None):
        self.executed.append((tool_name, args))
        if progress_callback:
            progress_callback(1, 2, "working")
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def close(self) -> None:
        self.closed = True


def _add_expert(app, server_id: int, name: str = "Oracle", **defn) -> int:
    """Create an expert entity linked to ``server_id``; return its id."""
    eid = app.db.add_entity(name, "expert", "#123")
    app.save_expert_definition(eid, server_id, "ask", **defn)
    return eid


# --- MCP server management ---------------------------------------------------

class TestMCPServerManagement:

    def test_add_get_update(self, app):
        notified = []
        app.set_update_callback(lambda state: notified.append(state))
        server = app.add_mcp_server("srv", "desc", "cmd", ["-x"], {"K": "v"})
        assert server["name"] == "srv" and server["args"] == ["-x"]
        assert [s["id"] for s in app.get_mcp_servers()] == [server["id"]]
        app.update_mcp_server(server["id"], description="new")
        assert app.db.get_mcp_server(server["id"])["description"] == "new"
        assert len(notified) == 2

    @pytest.mark.asyncio
    async def test_delete_closes_active_connection(self, app):
        server = app.add_mcp_server("srv", "", "cmd")
        provider = FakeMCPProvider()
        app._mcp_providers[server["id"]] = provider
        await app.delete_mcp_server(server["id"])
        assert provider.closed
        assert server["id"] not in app._mcp_providers
        assert app.db.get_mcp_server(server["id"]) is None

    def test_create_provider_picks_transport(self, app):
        from consensus.mcp_client import MCPToolProvider
        from consensus.mcp_http_client import MCPHTTPToolProvider
        stdio = app.add_mcp_server("a", "", "cmd")
        http = app.add_mcp_server("b", "", "", transport="http",
                                  url="http://x/mcp", headers={"H": "1"})
        assert isinstance(ConsensusApp._create_mcp_provider(stdio), MCPToolProvider)
        assert isinstance(ConsensusApp._create_mcp_provider(http), MCPHTTPToolProvider)

    @pytest.mark.asyncio
    async def test_connection_test_outcomes(self, app):
        assert await app.test_mcp_connection(999) == {
            "success": False, "error": "Server not found"}
        server = app.add_mcp_server("srv", "", "cmd")
        sid = server["id"]
        tool = ToolDefinition(name="t", description="d", parameters={})

        cases = [
            (FakeMCPProvider(connects=False),
             {"success": False, "error": "Failed to connect"}),
            (FakeMCPProvider(tools=[tool]),
             {"success": True, "tools": [{"name": "t", "description": "d"}]}),
            (FakeMCPProvider(raises=RuntimeError("boom")),
             {"success": False, "error": "boom"}),
        ]
        for provider, expected in cases:
            with patch.object(ConsensusApp, "_create_mcp_provider",
                              return_value=provider):
                assert await app.test_mcp_connection(sid) == expected
            assert provider.closed, "the probe connection must always close"

    @pytest.mark.asyncio
    async def test_connect_registers_provider(self, app):
        assert await app.connect_mcp_server(999) is False
        server = app.add_mcp_server("srv", "", "cmd")
        sid = server["id"]
        with patch.object(ConsensusApp, "_create_mcp_provider",
                          return_value=FakeMCPProvider(connects=False)):
            assert await app.connect_mcp_server(sid) is False
        assert sid not in app._mcp_providers

        provider = FakeMCPProvider(name="srv")
        with patch.object(ConsensusApp, "_create_mcp_provider",
                          return_value=provider):
            assert await app.connect_mcp_server(sid) is True
        assert app._mcp_providers[sid] is provider
        assert app.tool_registry._providers["srv"] is provider

        app.update_mcp_server(sid, enabled=False)
        assert await app.connect_mcp_server(sid) is False


# --- Experts -----------------------------------------------------------------

class TestExperts:

    @pytest.mark.asyncio
    async def test_list_experts(self, app):
        ctx = ToolContext(caller_entity_id=1, discussion_id=0)
        empty = await app._handle_list_experts({}, ctx)
        assert empty.content == "No experts are currently configured."

        ready = app.add_mcp_server("r", "", "cmd")["id"]
        down = app.add_mcp_server("d", "", "cmd")["id"]
        _add_expert(app, ready, "Oracle", description="knows things")
        _add_expert(app, down, "Sibyl")
        app._mcp_providers[ready] = FakeMCPProvider()

        result = await app._handle_list_experts({}, ctx)
        assert "- **Oracle** [ready]: knows things" in result.content
        assert "- **Sibyl** [unavailable]" in result.content
        assert len(app.get_expert_definitions()) == 2

    @pytest.mark.asyncio
    async def test_consult_reports_progress_and_records_message(self, app):
        sid = app.add_mcp_server("r", "", "cmd")["id"]
        _add_expert(app, sid, query_param_name="q",
                    default_arguments={"depth": 2})
        provider = FakeMCPProvider()
        app._mcp_providers[sid] = provider
        app.discussion.id = app.db.create_discussion("topic")
        progress = []
        app.on("tool_progress", progress.append)

        result = await app.consult_expert("oracle", "why?")

        assert result == {"content": "expert says hi", "is_error": False}
        assert provider.executed == [("ask", {"depth": 2, "q": "why?"})]
        assert progress[0]["message"] == "working"
        assert progress[0]["entity_name"] == "Oracle"
        assert app.discussion.messages[-1].content == "expert says hi"
        stored = app.db.get_messages(app.discussion.id)
        assert stored[-1]["content"] == "expert says hi"

    @pytest.mark.asyncio
    async def test_consult_timeout_is_an_error(self, app):
        sid = app.add_mcp_server("r", "", "cmd")["id"]
        _add_expert(app, sid, timeout_seconds=0)
        app._mcp_providers[sid] = FakeMCPProvider(delay=1)
        result = await app.consult_expert("Oracle", "q")
        assert result["is_error"]
        assert "timed out" in result["content"]


# --- Interactive user input --------------------------------------------------

class TestUserInput:

    @pytest.mark.asyncio
    async def test_submit_and_cancel(self, app):
        loop = asyncio.get_running_loop()
        f1, f2, f3 = loop.create_future(), loop.create_future(), loop.create_future()
        app._pending_user_inputs.update({"a": (f1, {}), "b": (f2, {}), "c": (f3, {})})

        assert app.submit_user_input("a", "yes") == {"ok": True}
        assert f1.result() == "yes"
        assert app.submit_user_input("a", "again") == {
            "error": "Request already resolved"}
        assert app.submit_user_input("zz", "x") == {
            "error": "No pending input request with that ID"}

        assert app.cancel_user_input("b") == {"ok": True}
        assert f2.cancelled() and "b" not in app._pending_user_inputs
        assert app.cancel_user_input("b") == {
            "error": "No pending input request with that ID"}

        app._cancel_pending_user_inputs()
        assert f3.cancelled() and app._pending_user_inputs == {}


# --- Tool management ---------------------------------------------------------

class TestToolManagement:

    @pytest.mark.asyncio
    async def test_list_and_assign(self, app):
        tools = await app.list_available_tools()
        names = {t["name"] for t in tools}
        assert {"consult_expert", "list_available_experts"} <= names
        assert set(tools[0]) == {"name", "description", "parameters", "provider"}

        eid = app.db.add_entity("A", "human", "#000")
        assert app.assign_tool_to_entity(eid, "web_search", "shared") is True
        assert [(t["tool_name"], t["access_mode"])
                for t in app.get_entity_tools(eid)] == [("web_search", "shared")]
        assert app.remove_entity_tool(eid, "web_search") is True
        assert app.get_entity_tools(eid) == []

        did = app.db.create_discussion("t")
        assert app.set_discussion_tool_override(did, eid, "web_search", False)
        [override] = app.db.get_discussion_tool_overrides(did, eid)
        assert (override["tool_name"], override["enabled"]) == ("web_search", 0)


# --- Documents from URL ------------------------------------------------------

class TestAddDocumentFromURL:

    @pytest.mark.asyncio
    async def test_unavailable(self, app):
        app.documents_available = False
        assert "failed to load" in (await app.add_document_from_url("http://x"))["error"]

    @pytest.mark.asyncio
    async def test_fetch_failures_become_errors(self, app, caplog):
        app.documents_available = True
        fetch = "consensus.tools_document.fetch_url_content"
        with patch(fetch, AsyncMock(side_effect=DocumentParseError("bad", hint="h"))):
            result = await app.add_document_from_url("http://x")
        assert result["error"].startswith("Failed to fetch URL: bad")

        with caplog.at_level(logging.ERROR, logger="consensus.app_api.documents"), \
             patch(fetch, AsyncMock(side_effect=KeyError("oops"))):
            result = await app.add_document_from_url("http://x")
        assert result["error"] == "Failed to fetch URL: 'oops'"
        assert "unexpectedly" in caplog.text

    @pytest.mark.asyncio
    async def test_success_forwards_to_add_document(self, app):
        app.documents_available = True
        fetched = (b"hello", "page.html", "text/html")
        with patch("consensus.tools_document.fetch_url_content",
                   AsyncMock(return_value=fetched)), \
             patch.object(app, "add_document",
                          AsyncMock(return_value={"document_id": 7})) as add:
            result = await app.add_document_from_url("http://x", 3, "T")
        assert result == {"document_id": 7}
        add.assert_awaited_once_with(
            filename="page.html", content_bytes=b"hello", mime_type="text/html",
            discussion_id=3, source_url="http://x", title="T")


# --- Images ------------------------------------------------------------------

# A real image: ingestion refuses raster bytes Pillow cannot decode.
PNG = make_image(1, 1)


class TestImages:

    @pytest.mark.asyncio
    async def test_pending_images_before_discussion_has_id(self, app, images_dir):
        result = await app.add_image("a.png", PNG, "image/png")
        iid = result["image_id"]
        assert app.discussion.pending_image_ids == [iid]
        assert [i["id"] for i in app.get_discussion_images()] == [iid]

        data, mime, name = app.get_image_data(iid)
        assert (data, mime, name) == (PNG, "image/png", "a.png")

        assert app.remove_discussion_image(iid) == {"success": True}
        assert app.discussion.pending_image_ids == []
        assert app.get_discussion_images() == []

    @pytest.mark.asyncio
    async def test_attached_images_and_delete(self, app, images_dir):
        did = app.db.create_discussion("t")
        iid = (await app.add_image("a.png", PNG, "image/png", did))["image_id"]
        assert [i["id"] for i in app.get_discussion_images(did)] == [iid]
        app.remove_discussion_image(iid, did)
        assert app.get_discussion_images(did) == []

        assert app.delete_image(iid) == {"success": True}
        assert list(images_dir.iterdir()) == []
        assert app.get_image_data(iid) is None

    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self, app, images_dir):
        iid = (await app.add_image("a.png", PNG, "image/png"))["image_id"]
        for f in images_dir.iterdir():
            f.unlink()
        assert app.get_image_data(iid) is None

    @pytest.mark.asyncio
    async def test_from_url(self, app, images_dir):
        fetch = "consensus.tools_image.fetch_image_from_url"
        with patch(fetch, AsyncMock(side_effect=FetchRejected("private address"))):
            result = await app.add_image_from_url("http://10.0.0.1/a.png")
        assert result == {"error": "Failed to fetch image: private address"}

        with patch(fetch, AsyncMock(return_value=(PNG, "a.png", "image/png"))):
            result = await app.add_image_from_url("http://x/a.png", title="Pic")
        assert result["title"] == "Pic"
        assert app.db.get_image(result["image_id"])["source_url"] == "http://x/a.png"


# --- Caught errors are logged (golden rule 6) --------------------------------

class TestCaughtErrorsAreLogged:
    """Each of these returned an error to the UI but left no trace in the log,
    so a user report had nothing to correlate against."""

    @pytest.mark.asyncio
    async def test_mcp_connection_failure(self, app, caplog):
        sid = app.add_mcp_server("srv", "", "cmd")["id"]
        with patch.object(ConsensusApp, "_create_mcp_provider",
                          return_value=FakeMCPProvider(raises=RuntimeError("boom"))), \
             caplog.at_level(logging.WARNING, logger="consensus.app_api"):
            await app.test_mcp_connection(sid)
        assert "srv" in caplog.text and "boom" in caplog.text

    @pytest.mark.asyncio
    async def test_image_fetch_failure(self, app, images_dir, caplog):
        with patch("consensus.tools_image.fetch_image_from_url",
                   AsyncMock(side_effect=FetchRejected("private address"))), \
             caplog.at_level(logging.WARNING, logger="consensus.app_api"):
            await app.add_image_from_url("http://10.0.0.1/a.png")
        assert "http://10.0.0.1/a.png" in caplog.text
        assert "private address" in caplog.text

    @pytest.mark.asyncio
    async def test_image_file_missing_on_disk(self, app, images_dir, caplog):
        iid = (await app.add_image("a.png", PNG, "image/png"))["image_id"]
        for f in images_dir.iterdir():
            f.unlink()
        with caplog.at_level(logging.WARNING, logger="consensus.app_api"):
            assert app.get_image_data(iid) is None
        assert f"image {iid}" in caplog.text

    @pytest.mark.asyncio
    async def test_desktop_image_data_url_failure(self, app, images_dir, caplog):
        from consensus.desktop import DesktopBridge
        iid = (await app.add_image("a.png", PNG, "image/png"))["image_id"]
        for f in images_dir.iterdir():
            f.unlink()
        bridge = DesktopBridge(app)
        try:
            with caplog.at_level(logging.WARNING, logger="consensus.desktop"):
                assert bridge.get_image_data_url(iid) == ""
        finally:
            bridge._loop.call_soon_threadsafe(bridge._loop.stop)
        assert f"image {iid}" in caplog.text

    @pytest.mark.parametrize("module, flag", [
        ("consensus.tools_memory", "memory_available"),
        ("consensus.tools_document", "documents_available"),
    ])
    def test_tool_package_that_fails_to_import_is_a_warning(
        self, tmp_path, monkeypatch, caplog, module, flag,
    ):
        """A broken package import used to disable the feature at INFO level.

        Every default install has the dependencies, so an ImportError here is
        a broken install or a broken import inside the package (e.g. after a
        split) — and the UI only says "see the application log".
        """
        import sys
        monkeypatch.setitem(sys.modules, module, None)  # import now raises
        with caplog.at_level(logging.WARNING, logger="consensus.app"):
            app = ConsensusApp(db_path=str(tmp_path / "broken.db"))
        assert getattr(app, flag) is False
        assert any(
            r.levelno == logging.WARNING and "failed to load" in r.getMessage()
            for r in caplog.records
        )


# --- Discussion setup --------------------------------------------------------

class TestDiscussionSetup:
    """Setup paths whose in-function imports only fail when called."""

    def test_methods(self, app):
        names = {m["name"] for m in app.list_discussion_methods()}
        assert {"open_discussion", "triage"} <= names
        assert "error" in app.set_discussion_method("no_such_method")

    def test_cost_limit_persists(self, app):
        app.discussion.id = app.db.create_discussion("t")
        state = app.set_cost_limit(1.5)
        assert state["cost_limit"] == 1.5
        assert app.db.get_discussion(app.discussion.id)["cost_limit"] == 1.5

    def test_default_context_strategy(self, app):
        assert "error" in app.set_default_context_strategy("bogus")
        app.discussion.id = app.db.create_discussion("t")
        app.set_default_context_strategy("sliding_window", 7)
        row = app.db.get_discussion(app.discussion.id)
        assert (row["default_context_strategy"],
                row["default_context_window_size"]) == ("sliding_window", 7)

    def test_member_context_strategy(self, app):
        eid = app.db.add_entity("A", "human", "#000")
        assert "error" in app.set_member_context_strategy(eid, "bogus")
        assert app.set_member_context_strategy(eid, "full") == {
            "error": "Entity not in discussion"}
        app.add_to_discussion(eid)
        app.set_member_context_strategy(eid, "summary", 5)
        assert app.discussion.member_context_configs[eid] == {
            "strategy": "summary", "window_size": 5}


# --- Public API guard --------------------------------------------------------

# Every public ConsensusApp method. desktop.py, server.py and
# mcp_server/handlers_write.py reach these by attribute access at call time,
# so a method lost in a split fails late (AttributeError in production), not
# at import or collection.
PUBLIC_API = {
    "add_document", "add_document_from_url", "add_image", "add_image_from_url",
    "add_mcp_server", "add_provider", "add_to_discussion",
    "assign_tool_to_entity", "cancel_user_input", "clear_request_api_keys",
    "complete_turn", "conclude_discussion", "connect_mcp_server",
    "consult_expert", "continue_discussion", "delete_discussions",
    "delete_document", "delete_entity", "delete_image", "delete_mcp_server",
    "delete_prompt", "delete_provider", "emit", "fetch_models",
    "generate_ai_turn", "get_discussion_documents", "get_discussion_images",
    "get_entities", "get_entity_tools", "get_expert_definitions",
    "get_export_data", "get_image_data", "get_inactive_entities",
    "get_mcp_servers", "get_prompts", "get_providers", "get_state",
    "list_available_tools", "list_discussion_methods", "load_discussion",
    "mediate", "off", "on", "pause_discussion", "reactivate_entity",
    "reassign_turn", "recommend_method", "remove_discussion_image",
    "remove_document", "remove_entity_tool", "remove_from_discussion",
    "reopen_discussion", "reset", "resolve_provider_api_key",
    "restore_discussion", "resume_discussion", "retry_method_switch",
    "save_entity", "save_expert_definition", "save_prompt",
    "set_cost_limit", "set_default_context_strategy",
    "set_discussion_method", "set_discussion_tool_override",
    "set_member_context_strategy", "set_moderator", "set_participant_role",
    "set_request_api_keys", "set_topic", "set_update_callback",
    "shutdown", "start_discussion", "submit_human_message",
    "submit_human_structured_message", "submit_moderator_message",
    "submit_user_input", "test_mcp_connection", "update_mcp_server",
    "update_provider",
}


def test_public_api_is_complete():
    """No public method is dropped (or silently added) by a refactor."""
    public = {n for n in dir(ConsensusApp)
              if not n.startswith("_") and callable(getattr(ConsensusApp, n))}
    assert public == PUBLIC_API
