"""Behaviour checks for moved ``app_api/`` paths the characterization suite missed.

Each test here closes a gap found by mutation during the PR #87 review: the
moved method was broken on purpose and the full suite still passed. Shares
the fakes with ``test_app_characterization``; the ``app`` and ``images_dir``
fixtures come from conftest.py.
"""

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock, patch

import pytest

from consensus.app import ConsensusApp
from consensus.tools import ToolContext, ToolResult
from tests.test_app_characterization import (
    PNG, PUBLIC_API, FakeMCPProvider, _add_expert,
)


# --- BYOK keys reach the catalog mixin ---------------------------------------

def test_request_keys_reach_get_providers(app):
    """set_request_api_keys (app.py) and get_providers (catalog.py) must share
    one ContextVar, or the web UI shows "no key" for a key the user entered."""
    pid = app.add_provider("byok", "http://x/v1")["id"]
    try:
        ConsensusApp.set_request_api_keys({str(pid): "sk-test"})
        [provider] = [p for p in app.get_providers() if p["id"] == pid]
        assert provider["has_key"] is True
    finally:
        ConsensusApp.clear_request_api_keys()
    [provider] = [p for p in app.get_providers() if p["id"] == pid]
    assert provider["has_key"] is False


# --- Documents: detach and delete --------------------------------------------

def _add_doc(app) -> int:
    """Insert a minimal document row directly; return its id."""
    return app.db.add_document("a.md", "A", "", "text/markdown", "upload",
                               None, "# A", 3, "[]")


def test_remove_document_detaches(app):
    did = app.db.create_discussion("t")
    other = app.db.create_discussion("u")
    doc = _add_doc(app)
    app.db.add_discussion_document(did, doc)
    app.db.add_discussion_document(other, doc)
    assert [d["id"] for d in app.get_discussion_documents(did)] == [doc]

    assert app.remove_document(doc, did) == {"success": True}
    assert app.get_discussion_documents(did) == []
    assert [d["id"] for d in app.get_discussion_documents(other)] == [doc], \
        "detaching from one discussion must not touch another"


def test_document_calls_default_to_current_discussion(app):
    app.discussion.id = app.db.create_discussion("t")
    doc = _add_doc(app)
    app.db.add_discussion_document(app.discussion.id, doc)
    assert [d["id"] for d in app.get_discussion_documents()] == [doc]
    app.remove_document(doc)
    assert app.get_discussion_documents() == []


def test_delete_document_removes_row(app):
    doc = _add_doc(app)
    assert app.delete_document(doc) == {"success": True}
    assert app.db.get_document(doc) is None
    assert app.delete_document(doc) == {"success": False}


# --- History -----------------------------------------------------------------

def test_export_carries_topic_and_messages(app):
    did = app.db.create_discussion("Export me")
    eid = app.db.add_entity("A", "human", "#000")
    app.db.add_message(did, eid, "hello world", "participant")
    data = app.get_export_data(did)
    assert data["topic"] == "Export me"
    assert [m["content"] for m in data["messages"]] == ["hello world"]
    assert app.get_export_data(999) == {"error": "Discussion not found"}


def test_delete_discussions_returns_state(app):
    did = app.db.create_discussion("t")
    result = app.delete_discussions([did])
    assert set(app.get_state()) <= set(result["state"])


@pytest.mark.asyncio
async def test_pause_cancels_pending_user_input(app):
    """An AI waiting in ask_user must not be left hanging by a pause."""
    future = asyncio.get_running_loop().create_future()
    app._pending_user_inputs["r"] = (future, {})
    app.pause_discussion()
    assert future.cancelled() and app._pending_user_inputs == {}


@pytest.mark.asyncio
async def test_conclude_cancels_pending_user_input(app):
    future = asyncio.get_running_loop().create_future()
    app._pending_user_inputs["r"] = (future, {})
    with patch("consensus.app_discussion_flow.conclude_discussion",
               AsyncMock(return_value={})):
        await app.conclude_discussion()
    assert future.cancelled() and app._pending_user_inputs == {}


# --- Experts -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_consult_expert_tool_is_wired_to_its_handler(app):
    """The path an AI participant takes: the registry's "experts" provider."""
    sid = app.add_mcp_server("r", "", "cmd")["id"]
    _add_expert(app, sid)
    provider = FakeMCPProvider()
    app._mcp_providers[sid] = provider
    experts = app.tool_registry._providers["experts"]
    ctx = ToolContext(caller_entity_id=1, discussion_id=0)

    result = await experts.execute(
        "consult_expert", {"expert_name": "Oracle", "query": "q"}, ctx)
    assert result.content == "expert says hi"
    assert provider.executed == [("ask", {"query": "q"})]

    listed = await experts.execute("list_available_experts", {}, ctx)
    assert "Oracle" in listed.content


@pytest.mark.asyncio
async def test_expert_error_is_not_recorded_as_a_message(app):
    sid = app.add_mcp_server("r", "", "cmd")["id"]
    _add_expert(app, sid)
    app._mcp_providers[sid] = FakeMCPProvider(
        result=ToolResult(content="server exploded", is_error=True))
    app.discussion.id = app.db.create_discussion("t")

    result = await app.consult_expert("Oracle", "q")
    assert result == {"content": "server exploded", "is_error": True}
    assert app.discussion.messages == []
    assert app.db.get_messages(app.discussion.id) == []


@pytest.mark.asyncio
async def test_expert_timeout_is_logged(app, caplog):
    sid = app.add_mcp_server("r", "", "cmd")["id"]
    _add_expert(app, sid, timeout_seconds=0)
    app._mcp_providers[sid] = FakeMCPProvider(delay=1)
    with caplog.at_level(logging.WARNING, logger="consensus.app_api"):
        await app.consult_expert("Oracle", "q")
    assert "Oracle" in caplog.text and "timed out" in caplog.text


# --- MCP connection test reports the reason ----------------------------------

@pytest.mark.asyncio
async def test_connection_test_reports_why_connect_failed(app):
    """Real stdio client, missing binary: the user must see why."""
    sid = app.add_mcp_server("srv", "", "/nonexistent/consensus-mcp-server")["id"]
    result = await app.test_mcp_connection(sid)
    assert result["success"] is False
    assert result["error"].startswith("Failed to connect: ")
    assert "/nonexistent/consensus-mcp-server" in result["error"]


@pytest.mark.asyncio
async def test_connection_test_names_a_silent_timeout(app):
    sid = app.add_mcp_server("srv", "", "cmd")["id"]
    with patch.object(ConsensusApp, "_create_mcp_provider",
                      return_value=FakeMCPProvider(raises=asyncio.TimeoutError())):
        result = await app.test_mcp_connection(sid)
    assert result == {"success": False, "error": "TimeoutError"}


@pytest.mark.asyncio
async def test_connection_test_bug_keeps_traceback(app, caplog):
    sid = app.add_mcp_server("srv", "", "cmd")["id"]
    with patch.object(ConsensusApp, "_create_mcp_provider",
                      return_value=FakeMCPProvider(raises=KeyError("name"))), \
         caplog.at_level(logging.WARNING, logger="consensus.app_api"):
        result = await app.test_mcp_connection(sid)
    assert result == {"success": False, "error": "'name'"}
    assert caplog.records[-1].exc_info is not None


def test_delete_missing_image_says_why(app):
    assert app.delete_image(999) == {"success": False, "error": "Image not found"}


def test_bad_discussion_method_is_logged(app, caplog):
    with caplog.at_level(logging.WARNING, logger="consensus.app_api"):
        assert "error" in app.set_discussion_method("no_such_method")
    assert "no_such_method" in caplog.text


# --- Unexpected faults keep their traceback ----------------------------------

@pytest.mark.asyncio
async def test_image_fetch_bug_keeps_traceback(app, images_dir, caplog):
    with patch("consensus.tools_image.fetch_image_from_url",
               AsyncMock(side_effect=TypeError("bad"))), \
         caplog.at_level(logging.WARNING, logger="consensus.app_api"):
        result = await app.add_image_from_url("http://x/a.png")
    assert result == {"error": "Failed to fetch image: bad"}
    assert caplog.records[-1].exc_info is not None


@pytest.mark.asyncio
async def test_desktop_path_traversal_keeps_traceback(app, images_dir, caplog):
    from consensus.desktop import DesktopBridge
    iid = (await app.add_image("a.png", PNG, "image/png"))["image_id"]
    bridge = DesktopBridge(app)
    try:
        with patch("consensus.tools_image.image_to_base64_url",
                   side_effect=ValueError("Path traversal detected")), \
             caplog.at_level(logging.WARNING, logger="consensus.desktop"):
            assert bridge.get_image_data_url(iid) == ""
    finally:
        bridge._loop.call_soon_threadsafe(bridge._loop.stop)
    assert caplog.records[-1].exc_info is not None


def test_desktop_start_discussion_runs_the_coroutine(app):
    """``start_discussion`` is async (#118): the bridge must hand JS its
    result, not an un-awaited coroutine."""
    from consensus.desktop import DesktopBridge
    bridge = DesktopBridge(app)
    try:
        result = bridge.start_discussion()
    finally:
        bridge._loop.call_soon_threadsafe(bridge._loop.stop)
    assert result == {"error": "No topic set"}


# --- Public API shape --------------------------------------------------------

ASYNC_PUBLIC_API = {
    "add_document", "add_document_from_url", "add_image", "add_image_from_url",
    "complete_turn", "conclude_discussion", "connect_mcp_server",
    "consult_expert", "delete_mcp_server", "fetch_models", "generate_ai_turn",
    "list_available_tools", "mediate", "recommend_method", "shutdown",
    "start_discussion",
    "test_mcp_connection",
}


def test_public_api_async_shape_is_stable():
    """desktop.py and server.py await exactly these; a move that drops or adds
    ``async`` breaks the caller at runtime, not at import."""
    assert ASYNC_PUBLIC_API <= PUBLIC_API
    actual = {n for n in PUBLIC_API
              if inspect.iscoroutinefunction(getattr(ConsensusApp, n))}
    assert actual == ASYNC_PUBLIC_API
