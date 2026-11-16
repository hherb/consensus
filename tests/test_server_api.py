"""Route tests for the JSON dispatch endpoint ``POST /api/<method>``.

The dispatch table maps a method name and a JSON body onto a
``ConsensusApp`` call. ``DISPATCH`` pins every entry: the app method it
reaches and the exact arguments it forwards (defaults included), so a
dropped entry, a crossed argument or a changed default fails here.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from consensus.app import ConsensusApp
from consensus.server import routes_api
from tests.server_helpers import (  # noqa: F401  (fixtures)
    api, fast_hashing, multi_client, server_home, signed_in, single_client,
)

# (api method, request body, ConsensusApp attribute, args, kwargs)
DISPATCH = [
    ("get_state", {}, "get_state", (), {}),
    ("add_provider", {"name": "P", "base_url": "http://x"},
     "add_provider", ("P", "http://x", "", ""), {}),
    ("add_provider", {"name": "P", "base_url": "http://x",
                      "api_key_env": "E", "api_key": "k"},
     "add_provider", ("P", "http://x", "E", "k"), {}),
    ("update_provider", {"provider_id": 3, "name": "N", "api_key": "k"},
     "update_provider", (3,), {"api_key": "k", "name": "N"}),
    ("update_provider", {"provider_id": 3},
     "update_provider", (3,), {"api_key": ""}),
    ("delete_provider", {"provider_id": 3}, "delete_provider", (3,), {}),
    ("fetch_models", {"provider_id": 3}, "fetch_models", (3,), {}),
    ("save_entity", {"name": "A", "entity_type": "ai"},
     "save_entity", (), {"name": "A", "entity_type": "ai"}),
    ("delete_entity", {"entity_id": 4}, "delete_entity", (4,), {}),
    ("reactivate_entity", {"entity_id": 4}, "reactivate_entity", (4,), {}),
    ("get_inactive_entities", {}, "get_inactive_entities", (), {}),
    ("save_prompt", {"prompt_id": None, "name": "p", "role": "moderator",
                     "target": "ai", "task": "turn", "content": "c"},
     "save_prompt", (), {"prompt_id": None, "name": "p", "role": "moderator",
                         "target": "ai", "task": "turn", "content": "c"}),
    ("delete_prompt", {"prompt_id": 5}, "delete_prompt", (5,), {}),
    ("add_to_discussion", {"entity_id": 4},
     "add_to_discussion", (4, False, False, "standard"), {}),
    ("add_to_discussion", {"entity_id": 4, "is_moderator": True,
                           "also_participant": True,
                           "participant_role": "devils_advocate"},
     "add_to_discussion", (4, True, True, "devils_advocate"), {}),
    ("remove_from_discussion", {"entity_id": 4},
     "remove_from_discussion", (4,), {}),
    ("set_moderator", {"entity_id": 4}, "set_moderator", (4, False), {}),
    ("set_moderator", {"entity_id": 4, "also_participant": True},
     "set_moderator", (4, True), {}),
    ("set_participant_role", {"entity_id": 4},
     "set_participant_role", (4, "standard"), {}),
    ("set_participant_role", {"entity_id": 4, "participant_role": "x"},
     "set_participant_role", (4, "x"), {}),
    ("set_topic", {"topic": "T"}, "set_topic", ("T",), {}),
    ("set_discussion_method", {"method_name": "delphi"},
     "set_discussion_method", ("delphi",), {}),
    ("list_discussion_methods", {}, "list_discussion_methods", (), {}),
    ("recommend_method", {}, "recommend_method", ("", ""), {}),
    ("recommend_method", {"topic": "T", "answer_type": "A"},
     "recommend_method", ("T", "A"), {}),
    ("start_discussion", {}, "start_discussion", (False, 0, 0.0), {}),
    ("start_discussion", {"moderator_participates": True, "max_rounds": "7",
                          "cost_limit": "1.5"},
     "start_discussion", (True, 7, 1.5), {}),
    ("set_cost_limit", {}, "set_cost_limit", (0.0,), {}),
    ("set_cost_limit", {"cost_limit": "2"}, "set_cost_limit", (2.0,), {}),
    ("set_default_context_strategy", {},
     "set_default_context_strategy", ("sliding_window", 20), {}),
    ("set_default_context_strategy", {"strategy": "full", "window_size": "5"},
     "set_default_context_strategy", ("full", 5), {}),
    ("set_member_context_strategy", {"entity_id": 4},
     "set_member_context_strategy", (4, "sliding_window", 20), {}),
    ("set_member_context_strategy", {"entity_id": 4, "strategy": "semantic",
                                     "window_size": 9},
     "set_member_context_strategy", (4, "semantic", 9), {}),
    ("submit_human_message", {"entity_id": 4, "content": "hi"},
     "submit_human_message", (4, "hi"), {}),
    ("submit_human_structured_message", {"entity_id": 4},
     "submit_human_structured_message", (4, {}), {}),
    ("submit_human_structured_message", {"entity_id": 4, "payload": {"a": 1}},
     "submit_human_structured_message", (4, {"a": 1}), {}),
    ("submit_moderator_message", {"content": "m"},
     "submit_moderator_message", ("m",), {}),
    ("generate_ai_turn", {}, "generate_ai_turn", (), {}),
    ("complete_turn", {}, "complete_turn", ("",), {}),
    ("complete_turn", {"moderator_summary": "s"}, "complete_turn", ("s",), {}),
    ("reassign_turn", {"entity_id": 4}, "reassign_turn", (4,), {}),
    ("mediate", {}, "mediate", ("",), {}),
    ("mediate", {"context": "c"}, "mediate", ("c",), {}),
    ("conclude", {}, "conclude_discussion", (), {}),
    ("pause_discussion", {}, "pause_discussion", (), {}),
    ("resume_discussion", {}, "resume_discussion", (), {}),
    ("retry_method_switch", {}, "retry_method_switch", (), {}),
    ("reopen_discussion", {}, "reopen_discussion", (), {}),
    ("continue_discussion", {}, "continue_discussion", ("",), {}),
    ("continue_discussion", {"content": "c"}, "continue_discussion", ("c",), {}),
    ("submit_user_input", {"request_id": "r", "content": "c"},
     "submit_user_input", ("r", "c"), {}),
    ("get_export_data", {"discussion_id": 8}, "get_export_data", (8,), {}),
    ("load_discussion", {"discussion_id": 8}, "load_discussion", (8,), {}),
    ("delete_discussions", {"discussion_ids": [8, 9]},
     "delete_discussions", ([8, 9],), {}),
    ("restore_discussion", {"discussion_id": 8},
     "restore_discussion", (8,), {}),
    ("reset", {}, "reset", (), {}),
    ("list_tools", {}, "list_available_tools", (), {}),
    ("get_entity_tools", {"entity_id": 4}, "get_entity_tools", (4,), {}),
    ("assign_tool", {"entity_id": 4, "tool_name": "t"},
     "assign_tool_to_entity", (4, "t", "private"), {}),
    ("assign_tool", {"entity_id": 4, "tool_name": "t", "access_mode": "shared"},
     "assign_tool_to_entity", (4, "t", "shared"), {}),
    ("remove_tool", {"entity_id": 4, "tool_name": "t"},
     "remove_entity_tool", (4, "t"), {}),
    ("set_tool_override", {"discussion_id": 8, "entity_id": 4,
                           "tool_name": "t", "enabled": False},
     "set_discussion_tool_override", (8, 4, "t", False), {}),
    ("add_mcp_server", {"name": "m"},
     "add_mcp_server", ("m", "", "", None, None),
     {"transport": "stdio", "url": "", "headers": None}),
    ("add_mcp_server", {"name": "m", "description": "d", "command": "c",
                        "args": ["a"], "env": {"K": "V"}, "transport": "http",
                        "url": "http://u", "headers": {"H": "1"}},
     "add_mcp_server", ("m", "d", "c", ["a"], {"K": "V"}),
     {"transport": "http", "url": "http://u", "headers": {"H": "1"}}),
    ("get_mcp_servers", {}, "get_mcp_servers", (), {}),
    ("update_mcp_server", {"server_id": 2, "name": "n", "enabled": False,
                           "transport": "ignored", "url": "ignored"},
     "update_mcp_server", (2,), {"name": "n", "enabled": False}),
    ("delete_mcp_server", {"server_id": 2}, "delete_mcp_server", (2,), {}),
    ("test_mcp_connection", {"server_id": 2},
     "test_mcp_connection", (2,), {}),
    ("save_expert_definition", {"entity_id": 4, "mcp_server_id": 2,
                                "tool_name": "t"},
     "save_expert_definition", (4, 2, "t", "", None, "query", 300), {}),
    ("save_expert_definition", {"entity_id": 4, "mcp_server_id": 2,
                                "tool_name": "t", "description": "d",
                                "default_arguments": {"a": 1},
                                "query_param_name": "q",
                                "timeout_seconds": 9},
     "save_expert_definition", (4, 2, "t", "d", {"a": 1}, "q", 9), {}),
    ("get_expert_definitions", {}, "get_expert_definitions", (), {}),
    ("consult_expert", {"expert_name": "e", "query": "q"},
     "consult_expert", ("e", "q"), {}),
]

# Every name the dispatch table answers to; the DISPATCH cases must cover all.
ALL_METHODS = {case[0] for case in DISPATCH}


def _recorder(calls: list, result: object, awaitable: bool,
              like: object = None):
    """A stand-in for a ``ConsensusApp`` method that records its arguments.

    *like* lends it that method's signature, so the dispatch's bind check
    (#134) judges the forwarded arguments against the real method.
    """
    if awaitable:
        async def record(self, *args, **kwargs):
            calls.append((args, kwargs))
            return result
    else:
        def record(self, *args, **kwargs):
            calls.append((args, kwargs))
            return result
    if like is not None:
        record.__signature__ = inspect.signature(like)
    return record


@pytest.mark.parametrize(
    "method,payload,attr,args,kwargs", DISPATCH,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(DISPATCH)],
)
async def test_dispatch_forwards_arguments(single_client, monkeypatch, method,
                                           payload, attr, args, kwargs):
    """Each method reaches its app method with exactly these arguments."""
    calls: list = []
    if attr == "get_state":
        real = ConsensusApp.get_state

        def record_state(self, *a, **kw):
            calls.append((a, kw))
            return real(self, *a, **kw)
        monkeypatch.setattr(ConsensusApp, attr, record_state)
    else:
        monkeypatch.setattr(ConsensusApp, attr,
                            _recorder(calls, {"ok": method}, awaitable=False,
                                      like=getattr(ConsensusApp, attr)))

    resp = await api(single_client, method, payload)
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert "state" in body
    assert calls[0] == (args, kwargs)
    if attr != "get_state":
        assert body["result"] == {"ok": method}
        assert len(calls) == 1


async def test_dispatch_awaits_coroutine_results(single_client, monkeypatch):
    """An async app method's result is awaited before it is serialised."""
    calls: list = []
    monkeypatch.setattr(ConsensusApp, "generate_ai_turn",
                        _recorder(calls, {"turn": 1}, awaitable=True))
    resp = await api(single_client, "generate_ai_turn")
    assert (await resp.json())["result"] == {"turn": 1}


def _route_table_methods() -> set[str]:
    """The keys of ``handle_api``'s ``handlers`` dict, read from the source."""
    tree = ast.parse(inspect.getsource(routes_api))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "handlers"
                        for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys}
    raise AssertionError("handlers table not found in routes_api")


async def test_dispatch_covers_every_route_method(single_client):
    """``DISPATCH`` and the route table name the same methods (#136).

    Read from the source, so an entry added to ``routes_api`` without a
    ``DISPATCH`` case fails here; an unlisted name is a 404.
    """
    assert _route_table_methods() == ALL_METHODS
    resp = await api(single_client, "no_such_method")
    assert resp.status == 404
    assert (await resp.json()) == {"error": "Unknown method: no_such_method"}


async def test_result_carries_the_real_state(single_client):
    """The response pairs the result with a fresh ``get_state()``."""
    resp = await api(single_client, "set_topic", {"topic": "Tea or coffee"})
    body = await resp.json()
    assert body["state"]["topic"] == "Tea or coffee"


async def test_app_error_is_a_500_with_its_message(single_client, monkeypatch,
                                                   caplog):
    """A raising app method answers 500 with the message, and is logged."""
    def boom(self):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(ConsensusApp, "get_inactive_entities", boom)
    resp = await api(single_client, "get_inactive_entities")
    assert resp.status == 500
    assert (await resp.json()) == {"error": "database is locked"}
    assert "API error: get_inactive_entities" in caplog.text


@pytest.mark.parametrize("method,payload,attr,needle", [
    ("delete_entity", {}, "delete_entity", "entity_id"),
    ("start_discussion", {"max_rounds": "ten"}, "start_discussion",
     "max_rounds must be an integer"),
    ("set_cost_limit", {"cost_limit": None}, "set_cost_limit",
     "cost_limit must be a number"),
    # json.dumps writes Infinity, which the server's json.loads accepts.
    ("start_discussion", {"max_rounds": float("inf")}, "start_discussion",
     "max_rounds must be an integer"),
    ("set_default_context_strategy", {"window_size": [20]},
     "set_default_context_strategy", "window_size must be an integer"),
    ("start_discussion", {"cost_limit": "cheap"}, "start_discussion",
     "cost_limit must be a number"),
    ("set_member_context_strategy", {"entity_id": 4, "window_size": "x"},
     "set_member_context_strategy", "window_size must be an integer"),
    ("save_entity", {"name": "A", "entity_type": "ai", "colour": "red"},
     "save_entity", "colour"),
    ("save_entity", {"name": "A"}, "save_entity", "entity_type"),
])
async def test_bad_arguments_are_a_400(single_client, monkeypatch, caplog,
                                       method, payload, attr, needle):
    """A client mistake in the body never reaches the app (#134).

    Missing keys, unconvertible values and unknown or missing keyword
    arguments are caught before the call, so they are the client's 400,
    not a logged server fault.
    """
    calls: list = []
    real = getattr(ConsensusApp, attr)

    def record(self, *args, **kwargs):
        calls.append((args, kwargs))
    record.__signature__ = inspect.signature(real)
    monkeypatch.setattr(ConsensusApp, attr, record)
    resp = await api(single_client, method, payload)
    assert resp.status == 400, await resp.text()
    error = (await resp.json())["error"]
    assert needle in error
    assert error.startswith(f"Invalid arguments for {method}:")
    assert calls == []
    assert "API error" not in caplog.text


@pytest.mark.parametrize("exc", [KeyError("k"), TypeError("t"),
                                 ValueError("v")])
async def test_same_exception_types_inside_the_app_stay_a_500(
        single_client, monkeypatch, exc):
    """The same exception types raised *by* the app are still server faults."""
    def boom(self):
        raise exc
    monkeypatch.setattr(ConsensusApp, "get_inactive_entities", boom)
    resp = await api(single_client, "get_inactive_entities")
    assert resp.status == 500


# The last: an integer past Python's int-string limit is a plain ValueError.
@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b"\xff",
                                  b'{"cost_limit": ' + b"1" * 5000 + b"}"])
async def test_unreadable_body_is_a_400(single_client, monkeypatch, body):
    """A body that is not a JSON object never reaches the app.

    It used to dispatch as ``{}``, so e.g. ``set_cost_limit`` silently
    saved a limit of 0.
    """
    calls: list = []
    monkeypatch.setattr(ConsensusApp, "set_cost_limit",
                        _recorder(calls, None, awaitable=False))
    resp = await single_client.post(
        "/api/set_cost_limit", data=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert (await resp.json()) == {"error": "Invalid JSON body"}
    assert calls == []


async def test_body_over_one_mib_is_a_413(single_client):
    """Only the upload routes lift aiohttp's 1 MiB body limit."""
    resp = await api(single_client, "get_state", {"pad": "a" * (1024 * 1024)})
    assert resp.status == 413


class TestByokKeys:
    """``X-API-Keys`` sets per-request keys and always clears them after."""

    async def _call(self, client, monkeypatch, header, fail=False):
        events: list = []
        monkeypatch.setattr(ConsensusApp, "set_request_api_keys",
                            lambda self, keys: events.append(("set", keys)))
        monkeypatch.setattr(ConsensusApp, "clear_request_api_keys",
                            lambda self: events.append(("clear",)))

        def list_methods(self):
            events.append(("call",))
            if fail:
                raise RuntimeError("x")
            return []
        monkeypatch.setattr(ConsensusApp, "list_discussion_methods",
                            list_methods)
        headers = {} if header is None else {"X-API-Keys": header}
        await api(client, "list_discussion_methods", headers=headers)
        return events

    async def test_keys_set_then_cleared(self, single_client, monkeypatch):
        events = await self._call(single_client, monkeypatch,
                                  '{"1": "sk-a", "2": "sk-b"}')
        assert events == [("set", {"1": "sk-a", "2": "sk-b"}), ("call",),
                          ("clear",)]

    async def test_provider_ids_become_strings(self, single_client,
                                               monkeypatch):
        events = await self._call(single_client, monkeypatch, '{"7": "k"}')
        assert events[0] == ("set", {"7": "k"})

    async def test_cleared_even_when_the_call_fails(self, single_client,
                                                    monkeypatch):
        events = await self._call(single_client, monkeypatch, '{"1": "k"}',
                                  fail=True)
        assert events[-1] == ("clear",)

    async def test_bad_arguments_set_no_keys(self, single_client,
                                             monkeypatch):
        """A 400 returns before the keys are set, so none are left behind."""
        events: list = []
        monkeypatch.setattr(ConsensusApp, "set_request_api_keys",
                            lambda self, keys: events.append(("set", keys)))
        resp = await api(single_client, "delete_entity",
                         headers={"X-API-Keys": '{"1": "k"}'})
        assert resp.status == 400
        assert events == []

    @pytest.mark.parametrize("header", [None, "", "not json", '["a"]', "42"])
    async def test_absent_or_malformed_header_sets_nothing(
            self, single_client, monkeypatch, header):
        events = await self._call(single_client, monkeypatch, header)
        assert events == [("call",)]


class TestMultiUserSessions:
    """``--multi-user``: per-session apps keyed by the session cookie."""

    async def test_first_call_sets_a_session_cookie(self, signed_in):
        resp = await api(signed_in, "get_state")
        assert resp.status == 200
        cookie = resp.cookies["consensus_sid"]
        from consensus.session import DEFAULT_SESSION_TTL
        assert int(cookie["max-age"]) == DEFAULT_SESSION_TTL
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"
        assert not cookie["secure"]

    async def test_session_state_persists_across_calls(self, signed_in):
        await api(signed_in, "set_topic", {"topic": "Mine"})
        body = await (await api(signed_in, "get_state")).json()
        assert body["state"]["topic"] == "Mine"

    async def test_sessions_are_isolated(self, signed_in):
        await api(signed_in, "set_topic", {"topic": "Mine"})
        signed_in.session.cookie_jar.clear(
            lambda c: c.key == "consensus_sid")
        body = await (await api(signed_in, "get_state")).json()
        assert body["state"]["topic"] == ""

    async def test_forged_session_id_is_replaced(self, signed_in):
        resp = await api(signed_in, "get_state",
                         cookies={"consensus_sid": "../../etc/passwd"})
        assert resp.cookies["consensus_sid"].value != "../../etc/passwd"

    async def test_at_capacity_is_a_503(self, signed_in, monkeypatch):
        from consensus.session import SessionManager

        async def full(self, sid):
            return None
        monkeypatch.setattr(SessionManager, "get_app", full)
        resp = await api(signed_in, "get_state")
        assert resp.status == 503
        assert "capacity" in (await resp.json())["error"]

    @pytest.mark.parametrize("method,payload", [
        ("no_such_method", {}), ("delete_entity", {}),
    ])
    async def test_error_replies_still_set_the_cookie(self, signed_in,
                                                      method, payload):
        """A session this call created is not orphaned by its error (#132)."""
        resp = await api(signed_in, method, payload)
        assert resp.status in (400, 404)
        assert "consensus_sid" in resp.cookies

    async def test_invalid_json_reply_sets_the_cookie(self, signed_in):
        resp = await signed_in.post(
            "/api/get_state", data=b"{bad",
            headers={"Content-Type": "application/json"})
        assert resp.status == 400
        assert "consensus_sid" in resp.cookies

    async def test_app_error_reply_sets_the_cookie(self, signed_in,
                                                   monkeypatch):
        def boom(self):
            raise RuntimeError("x")
        monkeypatch.setattr(ConsensusApp, "get_inactive_entities", boom)
        resp = await api(signed_in, "get_inactive_entities")
        assert resp.status == 500
        assert "consensus_sid" in resp.cookies
