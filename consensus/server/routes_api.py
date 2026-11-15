"""``POST /api/<method>``: the JSON dispatch onto ``ConsensusApp`` methods."""

import inspect
import logging
from functools import partial

from aiohttp import web

from .constants import CAPACITY_ERROR
from .context import (
    CTX_KEY, extract_api_keys, get_app_for_request, set_session_cookie,
)
from .request_io import read_json_object

logger = logging.getLogger(__name__)


async def handle_api(request: web.Request) -> web.StreamResponse:
    """Route API calls to the appropriate ConsensusApp method.

    Each table entry builds the call as a ``partial`` without running it, so
    a body that cannot make a valid call is a 400 (#134) while an exception
    raised inside the app method stays a logged 500.
    """
    ctx = request.app[CTX_KEY]
    method = request.match_info["method"]

    app, sid = await get_app_for_request(request)
    if app is None:
        resp = web.json_response(
            {"error": CAPACITY_ERROR},
            status=503,
        )
        return resp

    def reply(body: dict, status: int = 200) -> web.Response:
        # Every reply carries the cookie: a session created by this request
        # is otherwise orphaned, holding capacity until its TTL (#132).
        return set_session_cookie(
            ctx, web.json_response(body, status=status), sid)

    data = await read_json_object(request)
    if data is None:
        return reply({"error": "Invalid JSON body"}, 400)

    handlers = {
        # State
        "get_state": lambda: partial(app.get_state),
        # Providers
        "add_provider": lambda: partial(app.add_provider,
            data["name"], data["base_url"],
            data.get("api_key_env", ""),
            data.get("api_key", "")),
        "update_provider": lambda: partial(app.update_provider,
            data["provider_id"],
            api_key=data.get("api_key", ""),
            **{k: v for k, v in data.items()
               if k not in ("provider_id", "api_key")}),
        "delete_provider": lambda: partial(app.delete_provider,
            data["provider_id"]),
        "fetch_models": lambda: partial(app.fetch_models,
            data["provider_id"]),
        # Entity profiles
        "save_entity": lambda: partial(app.save_entity, **data),
        "delete_entity": lambda: partial(app.delete_entity, data["entity_id"]),
        "reactivate_entity": lambda: partial(app.reactivate_entity,
            data["entity_id"]),
        "get_inactive_entities": lambda: partial(app.get_inactive_entities),
        # Prompts
        "save_prompt": lambda: partial(app.save_prompt, **data),
        "delete_prompt": lambda: partial(app.delete_prompt, data["prompt_id"]),
        # Discussion setup
        "add_to_discussion": lambda: partial(app.add_to_discussion,
            data["entity_id"],
            data.get("is_moderator", False),
            data.get("also_participant", False),
            data.get("participant_role", "standard")),
        "remove_from_discussion": lambda: partial(app.remove_from_discussion,
            data["entity_id"]),
        "set_moderator": lambda: partial(app.set_moderator,
            data["entity_id"], data.get("also_participant", False)),
        "set_participant_role": lambda: partial(app.set_participant_role,
            data["entity_id"], data.get("participant_role", "standard")),
        "set_topic": lambda: partial(app.set_topic, data["topic"]),
        "set_discussion_method": lambda: partial(app.set_discussion_method,
            data["method_name"]),
        "list_discussion_methods": lambda: partial(app.list_discussion_methods),
        "recommend_method": lambda: partial(app.recommend_method,
            data.get("topic", ""), data.get("answer_type", "")),
        # Discussion lifecycle
        "start_discussion": lambda: partial(app.start_discussion,
            data.get("moderator_participates", False),
            int(data.get("max_rounds", 0)),
            float(data.get("cost_limit", 0.0))),
        "set_cost_limit": lambda: partial(app.set_cost_limit,
            float(data.get("cost_limit", 0.0))),
        "set_default_context_strategy": lambda: partial(app.set_default_context_strategy,
            data.get("strategy", "sliding_window"),
            int(data.get("window_size", 20))),
        "set_member_context_strategy": lambda: partial(app.set_member_context_strategy,
            data["entity_id"],
            data.get("strategy", "sliding_window"),
            int(data.get("window_size", 20))),
        "submit_human_message": lambda: partial(app.submit_human_message,
            data["entity_id"], data["content"]),
        "submit_human_structured_message":
            lambda: partial(app.submit_human_structured_message,
                data["entity_id"], data.get("payload", {})),
        "submit_moderator_message": lambda: partial(app.submit_moderator_message,
            data["content"]),
        "generate_ai_turn": lambda: partial(app.generate_ai_turn),
        "complete_turn": lambda: partial(app.complete_turn,
            data.get("moderator_summary", "")),
        "reassign_turn": lambda: partial(app.reassign_turn, data["entity_id"]),
        "mediate": lambda: partial(app.mediate, data.get("context", "")),
        "conclude": lambda: partial(app.conclude_discussion),
        "pause_discussion": lambda: partial(app.pause_discussion),
        "resume_discussion": lambda: partial(app.resume_discussion),
        "retry_method_switch": lambda: partial(app.retry_method_switch),
        "reopen_discussion": lambda: partial(app.reopen_discussion),
        "continue_discussion": lambda: partial(app.continue_discussion,
            data.get("content", "")),
        "submit_user_input": lambda: partial(app.submit_user_input,
            data["request_id"], data["content"]),
        # Export
        "get_export_data": lambda: partial(app.get_export_data,
            data["discussion_id"]),
        # History
        "load_discussion": lambda: partial(app.load_discussion,
            data["discussion_id"]),
        "delete_discussions": lambda: partial(app.delete_discussions,
            data["discussion_ids"]),
        "restore_discussion": lambda: partial(app.restore_discussion,
            data["discussion_id"]),
        "reset": lambda: partial(app.reset),
        # Tools
        "list_tools": lambda: partial(app.list_available_tools),
        "get_entity_tools": lambda: partial(app.get_entity_tools,
            data["entity_id"]),
        "assign_tool": lambda: partial(app.assign_tool_to_entity,
            data["entity_id"], data["tool_name"],
            data.get("access_mode", "private")),
        "remove_tool": lambda: partial(app.remove_entity_tool,
            data["entity_id"], data["tool_name"]),
        "set_tool_override": lambda: partial(app.set_discussion_tool_override,
            data["discussion_id"], data["entity_id"],
            data["tool_name"], data["enabled"]),
        # MCP servers
        "add_mcp_server": lambda: partial(app.add_mcp_server,
            data["name"], data.get("description", ""),
            data.get("command", ""), data.get("args"), data.get("env"),
            transport=data.get("transport", "stdio"),
            url=data.get("url", ""),
            headers=data.get("headers")),
        "get_mcp_servers": lambda: partial(app.get_mcp_servers),
        "update_mcp_server": lambda: partial(app.update_mcp_server,
            data["server_id"],
            **{k: v for k, v in data.items()
               if k in ("name", "description", "command", "args", "env", "enabled")}),
        "delete_mcp_server": lambda: partial(app.delete_mcp_server,
            data["server_id"]),
        "test_mcp_connection": lambda: partial(app.test_mcp_connection,
            data["server_id"]),
        # Expert definitions
        "save_expert_definition": lambda: partial(app.save_expert_definition,
            data["entity_id"], data["mcp_server_id"],
            data["tool_name"], data.get("description", ""),
            data.get("default_arguments"),
            data.get("query_param_name", "query"),
            data.get("timeout_seconds", 300)),
        "get_expert_definitions": lambda: partial(app.get_expert_definitions),
        "consult_expert": lambda: partial(app.consult_expert,
            data["expert_name"], data["query"]),
    }

    handler = handlers.get(method)
    if not handler:
        return reply({"error": f"Unknown method: {method}"}, 404)

    try:
        call = handler()
        inspect.signature(call.func).bind(*call.args, **call.keywords)
    except (KeyError, TypeError, ValueError, OverflowError) as e:
        # The client's mistake: a missing key, an unconvertible value (an
        # infinite or huge number overflows), or keyword arguments a
        # fixed-signature method does not take. Nothing has run yet.
        error = (f"missing field {e}" if isinstance(e, KeyError) else str(e))
        logger.warning("API %s: invalid arguments: %s", method, error)
        return reply({"error": f"Invalid arguments for {method}: {error}"},
                     400)

    # Extract BYOK keys and inject into app context
    api_keys = extract_api_keys(request)
    if api_keys:
        app.set_request_api_keys(api_keys)

    try:
        result: object = call()
        if inspect.isawaitable(result):
            result = await result
        return reply({"result": result, "state": app.get_state()})
    except Exception as e:
        logger.exception("API error: %s", method)
        return reply({"error": str(e)}, 500)
    finally:
        # Clear per-request keys after use
        if api_keys:
            app.clear_request_api_keys()
