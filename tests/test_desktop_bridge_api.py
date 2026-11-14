"""The desktop bridge's JS-facing API and its forwarding to ``ConsensusApp``.

pywebview exposes every public method of the ``js_api`` object
(``dir()`` + ``inspect.ismethod``, inherited ones included) and the frontend
calls them by name, so a method lost in a refactor fails only when a user
clicks something. These tests pin the method set against the frontend's
calls and check that each forwarding method reaches the right
``ConsensusApp`` method with the right arguments, sync or bridged async.
"""

import inspect
import re
from pathlib import Path

import pytest

import consensus
from consensus.desktop import DesktopBridge
from tests.desktop_helpers import bridge  # noqa: F401

STATIC_DIR = Path(consensus.__file__).parent / "static"

#: Every method pywebview exposes to JavaScript.
BRIDGE_API = {
    "add_document_from_url", "add_image_from_url", "add_mcp_server",
    "add_provider", "add_to_discussion", "assign_tool", "complete_turn",
    "conclude", "consult_expert", "continue_discussion", "delete_discussions",
    "delete_document", "delete_entity", "delete_image", "delete_mcp_server",
    "delete_prompt", "delete_provider", "fetch_models", "generate_ai_turn",
    "get_discussion_documents", "get_discussion_images", "get_entity_tools",
    "get_expert_definitions", "get_export_data", "get_image_data_url",
    "get_inactive_entities", "get_mcp_servers", "get_memory_config",
    "get_state", "list_discussion_methods", "list_tools", "load_discussion",
    "mediate", "open_evaluation", "pause_discussion", "reactivate_entity",
    "reassign_turn", "recommend_method", "remove_document",
    "remove_from_discussion", "remove_image", "remove_tool",
    "reopen_discussion", "reset", "restore_discussion", "resume_discussion",
    "retry_method_switch", "save_entity", "save_expert_definition",
    "save_file", "save_memory_config", "save_prompt", "set_cost_limit",
    "set_default_context_strategy", "set_discussion_method",
    "set_member_context_strategy", "set_moderator", "set_participant_role",
    "set_tool_override", "set_topic", "start_discussion",
    "submit_human_message", "submit_human_structured_message",
    "submit_moderator_message", "submit_user_input", "test_mcp_connection",
    "test_memory_connection", "update_mcp_server", "update_provider",
    "upload_document", "upload_image",
}


def _exposed(obj) -> set[str]:
    """The names pywebview's ``get_functions`` would expose for *obj*."""
    return {n for n in dir(obj)
            if not n.startswith("_") and inspect.ismethod(getattr(obj, n))}


def _js_calls() -> set[str]:
    names: set[str] = set()
    for path in STATIC_DIR.rglob("*.js"):
        names |= set(re.findall(r"pywebview\.api\.([A-Za-z_]\w*)",
                                path.read_text(encoding="utf-8")))
    return names


class TestExposedApi:
    def test_exposed_methods_match_the_pinned_api(self, bridge):
        assert _exposed(bridge) == BRIDGE_API

    def test_every_frontend_call_exists(self, bridge):
        calls = _js_calls()
        assert calls, "the scan found no pywebview.api calls"
        assert calls - _exposed(bridge) == set()

    def test_every_method_is_documented(self):
        for name in BRIDGE_API:
            assert inspect.getdoc(getattr(DesktopBridge, name)), name


SENTINEL = {"sentinel": True}

#: ``(bridge method, args, kwargs, app method, expected args, expected
#: kwargs, app method is async)``.
FORWARDS = [
    ("get_state", (), {}, "get_state", (), {}, False),
    ("add_provider", ("n", "u"), {}, "add_provider", ("n", "u", "", ""), {},
     False),
    ("update_provider", (1, "n", "u", "E", "k"), {}, "update_provider", (1,),
     {"api_key": "k", "name": "n", "base_url": "u", "api_key_env": "E"},
     False),
    ("update_provider", (1,), {}, "update_provider", (1,),
     {"api_key": "", "api_key_env": ""}, False),
    ("update_provider", (1,), {"api_key_env": None}, "update_provider", (1,),
     {"api_key": ""}, False),
    ("delete_provider", (1,), {}, "delete_provider", (1,), {}, False),
    ("fetch_models", (1,), {}, "fetch_models", (1,), {}, True),
    ("save_entity", ("n", "ai"), {}, "save_entity",
     ("n", "ai", "#3b82f6", 0, "", 0.7, 1024, "", 0), {}, False),
    ("delete_entity", (1,), {}, "delete_entity", (1,), {}, False),
    ("reactivate_entity", (1,), {}, "reactivate_entity", (1,), {}, False),
    ("get_inactive_entities", (), {}, "get_inactive_entities", (), {}, False),
    ("save_prompt", (1, "n", "r", "t", "k", "c"), {}, "save_prompt",
     (1, "n", "r", "t", "k", "c"), {}, False),
    ("delete_prompt", (1,), {}, "delete_prompt", (1,), {}, False),
    ("add_to_discussion", (1,), {}, "add_to_discussion",
     (1, False, False, "standard"), {}, False),
    ("remove_from_discussion", (1,), {}, "remove_from_discussion", (1,), {},
     False),
    ("set_moderator", (1,), {}, "set_moderator", (1, False), {}, False),
    ("set_participant_role", (1,), {}, "set_participant_role",
     (1, "standard"), {}, False),
    ("set_topic", ("t",), {}, "set_topic", ("t",), {}, False),
    ("set_discussion_method", ("m",), {}, "set_discussion_method", ("m",), {},
     False),
    ("list_discussion_methods", (), {}, "list_discussion_methods", (), {},
     False),
    ("recommend_method", ("t", "a"), {}, "recommend_method", ("t", "a"), {},
     True),
    ("start_discussion", (), {}, "start_discussion", (False, 0, 0.0), {},
     True),
    ("set_cost_limit", (2.0,), {}, "set_cost_limit", (2.0,), {}, False),
    ("set_default_context_strategy", ("s", "5"), {},
     "set_default_context_strategy", ("s", 5), {}, False),
    ("set_member_context_strategy", (1, "s", "5"), {},
     "set_member_context_strategy", (1, "s", 5), {}, False),
    ("submit_human_message", (1, "c"), {}, "submit_human_message", (1, "c"),
     {}, False),
    ("submit_human_structured_message", (1, {"a": 1}), {},
     "submit_human_structured_message", (1, {"a": 1}), {}, False),
    ("submit_moderator_message", ("c",), {}, "submit_moderator_message",
     ("c",), {}, False),
    ("submit_user_input", ("r", "c"), {}, "submit_user_input", ("r", "c"), {},
     False),
    ("generate_ai_turn", (), {}, "generate_ai_turn", (), {}, True),
    ("complete_turn", (), {}, "complete_turn", ("",), {}, True),
    ("reassign_turn", (1,), {}, "reassign_turn", (1,), {}, False),
    ("mediate", (), {}, "mediate", ("",), {}, True),
    ("conclude", (), {}, "conclude_discussion", (), {}, True),
    ("list_tools", (), {}, "list_available_tools", (), {}, True),
    ("get_entity_tools", (1,), {}, "get_entity_tools", (1,), {}, False),
    ("assign_tool", (1, "t"), {}, "assign_tool_to_entity",
     (1, "t", "private"), {}, False),
    ("remove_tool", (1, "t"), {}, "remove_entity_tool", (1, "t"), {}, False),
    ("set_tool_override", (1, 2, "t", True), {},
     "set_discussion_tool_override", (1, 2, "t", True), {}, False),
    ("get_mcp_servers", (), {}, "get_mcp_servers", (), {}, False),
    ("add_mcp_server", ("n", "d", "c"), {}, "add_mcp_server",
     ("n", "d", "c", None, None),
     {"transport": "stdio", "url": "", "headers": None}, False),
    ("update_mcp_server", (1,), {"name": "x"}, "update_mcp_server", (1,),
     {"name": "x"}, False),
    ("delete_mcp_server", (1,), {}, "delete_mcp_server", (1,), {}, True),
    ("test_mcp_connection", (1,), {}, "test_mcp_connection", (1,), {}, True),
    ("save_expert_definition", (1, 2, "t"), {}, "save_expert_definition",
     (1, 2, "t", "", None, "query", 300), {}, False),
    ("get_expert_definitions", (), {}, "get_expert_definitions", (), {},
     False),
    ("consult_expert", ("e", "q"), {}, "consult_expert", ("e", "q"), {},
     True),
    ("get_discussion_documents", (), {}, "get_discussion_documents", (0,), {},
     False),
    ("remove_document", (1,), {}, "remove_document", (1, 0), {}, False),
    ("delete_document", (1,), {}, "delete_document", (1,), {}, False),
    ("add_image_from_url", ("u",), {}, "add_image_from_url", (),
     {"url": "u", "discussion_id": 0, "title": ""}, True),
    ("get_discussion_images", (), {}, "get_discussion_images", (0,), {},
     False),
    ("remove_image", (1,), {}, "remove_discussion_image", (1, 0), {}, False),
    ("delete_image", (1,), {}, "delete_image", (1,), {}, False),
    ("get_export_data", (1,), {}, "get_export_data", (1,), {}, False),
    ("pause_discussion", (), {}, "pause_discussion", (), {}, False),
    ("resume_discussion", (), {}, "resume_discussion", (), {}, False),
    ("retry_method_switch", (), {}, "retry_method_switch", (), {}, False),
    ("reopen_discussion", (), {}, "reopen_discussion", (), {}, False),
    ("continue_discussion", ("c",), {}, "continue_discussion", ("c",), {},
     False),
    ("load_discussion", (1,), {}, "load_discussion", (1,), {}, False),
    ("delete_discussions", (["1", 2],), {}, "delete_discussions", ([1, 2],),
     {}, False),
    ("restore_discussion", ("3",), {}, "restore_discussion", (3,), {}, False),
    ("reset", (), {}, "reset", (), {}, False),
]


def _recorder(is_async: bool):
    calls: list[tuple] = []
    if is_async:
        async def fake(*args, **kwargs):
            calls.append((args, kwargs))
            return SENTINEL
    else:
        def fake(*args, **kwargs):
            calls.append((args, kwargs))
            return SENTINEL
    return fake, calls


@pytest.mark.parametrize(
    "method,args,kwargs,target,exp_args,exp_kwargs,is_async", FORWARDS,
    ids=[f"{f[0]}-{i}" for i, f in enumerate(FORWARDS)])
def test_forwards_to_the_app(bridge, monkeypatch, method, args, kwargs,
                             target, exp_args, exp_kwargs, is_async):
    fake, calls = _recorder(is_async)
    monkeypatch.setattr(bridge.app, target, fake)
    assert getattr(bridge, method)(*args, **kwargs) is SENTINEL
    assert calls == [(exp_args, exp_kwargs)]


def test_app_async_methods_are_bridged(bridge):
    """A forward of an async app method must go through ``_run_async``:
    returning the coroutine hands JS an object it cannot serialise."""
    for _, _, _, target, _, _, is_async in FORWARDS:
        assert inspect.iscoroutinefunction(
            getattr(type(bridge.app), target)) == is_async, target
