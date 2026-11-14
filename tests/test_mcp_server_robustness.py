"""Defects the #61 characterization net found, and protocol robustness.

Moved out of ``tests/test_mcp_server_handlers.py`` (which keeps the
characterization net itself) so both files stay under the 500-line budget.
A ``run_discussion`` that failed or stopped early must say so in its result,
and malformed stdio input must get an error reply, or none for a
notification, without stopping the server.
"""

import asyncio
import json
import sqlite3

import pytest

from consensus.app import ConsensusApp
from consensus.mcp_server import ConsensusMCPServer
from tests.document_helpers import patch_where_defined
from tests.eval_runner_helpers import is_summary, is_turn, llm  # noqa: F401
from tests.mcp_server_helpers import (  # noqa: F401
    INIT, _ai_entities, _stdio, call, server,
)


def _tool_call(params) -> dict:
    """A ``tools/call`` request with id 3 and the given ``params``."""
    return {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": params}


async def _run(server, **args) -> dict:
    """Run a one-round discussion between two AI entities; return the JSON."""
    text, _ = await asyncio.wait_for(
        call(server, "run_discussion", {"topic": "T", "max_rounds": 1, **args}),
        timeout=10)
    return json.loads(text)


def _wrap_complete_turn(monkeypatch, after) -> None:
    """Run ``after(app)`` once each real ``complete_turn`` has returned."""
    real = ConsensusApp.complete_turn

    async def complete_turn(self):
        result = await real(self)
        after(self)
        return result

    monkeypatch.setattr(ConsensusApp, "complete_turn", complete_turn)


class TestRunDiscussionReportsFailures:
    """A failed or stopped run must not read as a clean conclusion."""

    async def test_clean_run_has_no_errors(self, server, llm):
        _ai_entities(server._db)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["error"] == ""
        assert data["turn_errors"] == []

    async def test_skipped_turns_are_listed(self, server, llm):
        _ai_entities(server._db)
        llm.fail_when = is_turn
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert len(data["turn_errors"]) == 2
        assert data["turn_errors"][0].startswith("AI0: ")
        assert "500" in data["turn_errors"][0]

    async def test_failed_completion_stops_the_run(self, server, llm):
        """It used to retry the same turn until the 10-minute timeout."""
        _ai_entities(server._db)
        llm.fail_when = is_summary
        llm.yield_each_call = True  # lets the guard below fire if it loops
        text, _ = await asyncio.wait_for(
            call(server, "run_discussion", {"topic": "T", "max_rounds": 1}),
            timeout=10)
        data = json.loads(text)
        assert data["error"].startswith("Turn completion failed: ")
        assert sum(is_turn(c) for c in llm.calls) == 1

    async def test_timeout_is_reported(self, server, llm, monkeypatch):
        _ai_entities(server._db)
        patch_where_defined(monkeypatch,
                            ConsensusMCPServer._handle_run_discussion,
                            "RUN_DISCUSSION_TIMEOUT_SECONDS", 0)
        data = json.loads((await call(server, "run_discussion",
                                      {"topic": "T", "max_rounds": 1}))[0])
        assert data["error"] == "Discussion timed out after 0 s"

    async def test_clean_run_says_why_and_how_far(self, server, llm):
        _ai_entities(server._db)
        data = await _run(server)
        assert data["stop_reason"] == "max_rounds"
        assert data["rounds_completed"] == 1  # used to report 2

    async def test_failed_turn_stops_the_run(self, server, llm, monkeypatch):
        """A turn that failed without being skipped is not retried."""
        _ai_entities(server._db)
        calls = []

        async def generate_ai_turn(self):
            calls.append(1)
            return {"error": "boom"}

        monkeypatch.setattr(ConsensusApp, "generate_ai_turn", generate_ai_turn)
        data = await _run(server)
        assert data["error"] == "Turn failed: boom"
        assert data["turn_errors"] == [] and data["stop_reason"] == ""
        assert calls == [1]

    async def test_no_ai_speaker_due_is_reported(self, server, llm,
                                                 monkeypatch):
        _ai_entities(server._db)
        _wrap_complete_turn(
            monkeypatch,
            lambda app: setattr(app.discussion, "turn_order", []))
        data = await _run(server)
        assert data["error"] == "Discussion stopped: no AI speaker due"

    async def test_loop_exception_is_reported_and_concluded(
            self, server, llm, monkeypatch):
        """It used to escape as a bare error, leaving the run "active"."""
        _ai_entities(server._db)

        def locked(app):
            raise sqlite3.OperationalError("database is locked")

        _wrap_complete_turn(monkeypatch, locked)
        data = await _run(server)
        assert data["error"] == ("Turn loop failed: "
                                 "OperationalError: database is locked")
        row = server._db.get_discussion(data["discussion_id"])
        assert row["status"] != "active"

    async def test_moderator_must_be_a_participant(self, server):
        a, b, c = _ai_entities(server._db, 3)
        text, is_error = await call(server, "run_discussion", {
            "topic": "T", "entity_ids": [a, b], "moderator_id": c})
        assert is_error
        assert text == f"Error: moderator_id {c} is not in entity_ids."

    async def test_duplicate_entity_is_reported(self, server):
        a, _ = _ai_entities(server._db)
        text, is_error = await call(server, "run_discussion", {
            "topic": "T", "entity_ids": [a, a]})
        assert is_error
        assert text == (f"Error: Could not add entity {a}: "
                        "AI0 is already in the discussion")

    async def test_app_is_shut_down(self, server, llm, monkeypatch):
        """Its MCP provider subprocesses used to leak on every call."""
        _ai_entities(server._db)
        shut = []

        async def shutdown(self):
            shut.append(self)

        monkeypatch.setattr(ConsensusApp, "shutdown", shutdown)
        await _run(server)
        assert len(shut) == 1


class TestStdioRobustness:
    """Malformed input gets an error reply; it must not stop the server."""

    async def _replies(self, server, monkeypatch, lines):
        out = _stdio(monkeypatch, lines + [INIT])
        await server.run()
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        assert replies[-1]["id"] == 7, "the server stopped answering"
        return replies[:-1]

    async def test_unparseable_line(self, server, monkeypatch, caplog):
        replies = await self._replies(server, monkeypatch, ["{oops\n"])
        assert [r["error"]["code"] for r in replies] == [-32700]
        assert "Unparseable JSON-RPC line" in caplog.text

    @pytest.mark.parametrize("message, code", [
        ([1, 2], -32600),
        (_tool_call(None), -32602),       # no params: an unknown tool ""
        (_tool_call([1]), -32602),
        (_tool_call({"name": ["x"]}), -32602),  # used to kill the loop
        (_tool_call({"name": {"x": 1}}), -32602),
        (_tool_call({"name": "list_discussions", "arguments": "hi"}),
         -32602),
    ])
    async def test_malformed_request(self, server, monkeypatch, message,
                                     code):
        replies = await self._replies(server, monkeypatch,
                                      [json.dumps(message) + "\n"])
        assert [r["error"]["code"] for r in replies] == [code]

    async def test_unknown_notification_gets_no_reply(self, server,
                                                      monkeypatch):
        line = json.dumps({"jsonrpc": "2.0",
                           "method": "notifications/cancelled"}) + "\n"
        assert await self._replies(server, monkeypatch, [line]) == []

    async def test_server_reports_the_package_version(self, server):
        import consensus
        resp = await server.handle_request(json.loads(INIT))
        assert resp["result"]["serverInfo"]["version"] == consensus.__version__

    @pytest.mark.parametrize("message", [
        {"jsonrpc": "2.0", "method": "initialize"},
        {"jsonrpc": "2.0", "method": "notifications/x", "params": [1]},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": ["x"]}},
    ])
    async def test_no_notification_is_answered(self, server, monkeypatch,
                                               message):
        line = json.dumps(message) + "\n"
        assert await self._replies(server, monkeypatch, [line]) == []

    async def test_dispatch_crash_is_answered_and_survived(
            self, server, monkeypatch, caplog):
        real = server.handle_request

        async def handle_request(msg):
            if msg.get("id") == 3:
                raise RuntimeError("dispatch bug")
            return await real(msg)

        monkeypatch.setattr(server, "handle_request", handle_request)
        replies = await self._replies(
            server, monkeypatch, [json.dumps(_tool_call({})) + "\n"])
        assert replies == [{"jsonrpc": "2.0", "id": 3, "error": {
            "code": -32603, "message": "Internal error"}}]
        assert "dispatch bug" in caplog.text

    async def test_tool_error_names_the_exception(self, server, monkeypatch):
        """A message-less exception used to read as a bare "Error: "."""
        async def handler(args):
            raise TimeoutError()

        monkeypatch.setitem(server._tools["list_discussions"], "handler",
                            handler)
        text, is_error = await call(server, "list_discussions", {})
        assert is_error and text == "Error: TimeoutError"
