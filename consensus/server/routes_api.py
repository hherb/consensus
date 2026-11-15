"""``POST /api/<method>``: the JSON dispatch onto ``ConsensusApp`` methods."""

import inspect
import json
import logging

from aiohttp import web

from .context import (
    CTX_KEY, extract_api_keys, get_app_for_request, set_session_cookie,
)

logger = logging.getLogger(__name__)


async def handle_api(request: web.Request) -> web.StreamResponse:
    """Route API calls to the appropriate ConsensusApp method."""
    ctx = request.app[CTX_KEY]
    method = request.match_info["method"]

    app, sid = await get_app_for_request(request)
    if app is None:
        resp = web.json_response(
            {"error": "Server is at capacity. Please try again later."},
            status=503,
        )
        return resp

    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        data = {}

    # Extract BYOK keys and inject into app context
    api_keys = extract_api_keys(request)
    if api_keys:
        app.set_request_api_keys(api_keys)

    handlers = {
        # State
        "get_state": lambda: app.get_state(),
        # Providers
        "add_provider": lambda: app.add_provider(
            data["name"], data["base_url"],
            data.get("api_key_env", ""),
            data.get("api_key", "")),
        "update_provider": lambda: app.update_provider(
            data["provider_id"],
            api_key=data.get("api_key", ""),
            **{k: v for k, v in data.items()
               if k not in ("provider_id", "api_key")}),
        "delete_provider": lambda: app.delete_provider(
            data["provider_id"]),
        "fetch_models": lambda: app.fetch_models(
            data["provider_id"]),
        # Entity profiles
        "save_entity": lambda: app.save_entity(**data),
        "delete_entity": lambda: app.delete_entity(data["entity_id"]),
        "reactivate_entity": lambda: app.reactivate_entity(
            data["entity_id"]),
        "get_inactive_entities": lambda: app.get_inactive_entities(),
        # Prompts
        "save_prompt": lambda: app.save_prompt(**data),
        "delete_prompt": lambda: app.delete_prompt(data["prompt_id"]),
        # Discussion setup
        "add_to_discussion": lambda: app.add_to_discussion(
            data["entity_id"],
            data.get("is_moderator", False),
            data.get("also_participant", False),
            data.get("participant_role", "standard")),
        "remove_from_discussion": lambda: app.remove_from_discussion(
            data["entity_id"]),
        "set_moderator": lambda: app.set_moderator(
            data["entity_id"], data.get("also_participant", False)),
        "set_participant_role": lambda: app.set_participant_role(
            data["entity_id"], data.get("participant_role", "standard")),
        "set_topic": lambda: app.set_topic(data["topic"]),
        "set_discussion_method": lambda: app.set_discussion_method(
            data["method_name"]),
        "list_discussion_methods": lambda: app.list_discussion_methods(),
        "recommend_method": lambda: app.recommend_method(
            data.get("topic", ""), data.get("answer_type", "")),
        # Discussion lifecycle
        "start_discussion": lambda: app.start_discussion(
            data.get("moderator_participates", False),
            int(data.get("max_rounds", 0)),
            float(data.get("cost_limit", 0.0))),
        "set_cost_limit": lambda: app.set_cost_limit(
            float(data.get("cost_limit", 0.0))),
        "set_default_context_strategy": lambda: app.set_default_context_strategy(
            data.get("strategy", "sliding_window"),
            int(data.get("window_size", 20))),
        "set_member_context_strategy": lambda: app.set_member_context_strategy(
            data["entity_id"],
            data.get("strategy", "sliding_window"),
            int(data.get("window_size", 20))),
        "submit_human_message": lambda: app.submit_human_message(
            data["entity_id"], data["content"]),
        "submit_human_structured_message":
            lambda: app.submit_human_structured_message(
                data["entity_id"], data.get("payload", {})),
        "submit_moderator_message": lambda: app.submit_moderator_message(
            data["content"]),
        "generate_ai_turn": lambda: app.generate_ai_turn(),
        "complete_turn": lambda: app.complete_turn(
            data.get("moderator_summary", "")),
        "reassign_turn": lambda: app.reassign_turn(data["entity_id"]),
        "mediate": lambda: app.mediate(data.get("context", "")),
        "conclude": lambda: app.conclude_discussion(),
        "pause_discussion": lambda: app.pause_discussion(),
        "resume_discussion": lambda: app.resume_discussion(),
        "retry_method_switch": lambda: app.retry_method_switch(),
        "reopen_discussion": lambda: app.reopen_discussion(),
        "continue_discussion": lambda: app.continue_discussion(
            data.get("content", "")),
        "submit_user_input": lambda: app.submit_user_input(
            data["request_id"], data["content"]),
        # Export
        "get_export_data": lambda: app.get_export_data(
            data["discussion_id"]),
        # History
        "load_discussion": lambda: app.load_discussion(
            data["discussion_id"]),
        "delete_discussions": lambda: app.delete_discussions(
            data["discussion_ids"]),
        "restore_discussion": lambda: app.restore_discussion(
            data["discussion_id"]),
        "reset": lambda: app.reset(),
        # Tools
        "list_tools": lambda: app.list_available_tools(),
        "get_entity_tools": lambda: app.get_entity_tools(
            data["entity_id"]),
        "assign_tool": lambda: app.assign_tool_to_entity(
            data["entity_id"], data["tool_name"],
            data.get("access_mode", "private")),
        "remove_tool": lambda: app.remove_entity_tool(
            data["entity_id"], data["tool_name"]),
        "set_tool_override": lambda: app.set_discussion_tool_override(
            data["discussion_id"], data["entity_id"],
            data["tool_name"], data["enabled"]),
        # MCP servers
        "add_mcp_server": lambda: app.add_mcp_server(
            data["name"], data.get("description", ""),
            data.get("command", ""), data.get("args"), data.get("env"),
            transport=data.get("transport", "stdio"),
            url=data.get("url", ""),
            headers=data.get("headers")),
        "get_mcp_servers": lambda: app.get_mcp_servers(),
        "update_mcp_server": lambda: app.update_mcp_server(
            data["server_id"],
            **{k: v for k, v in data.items()
               if k in ("name", "description", "command", "args", "env", "enabled")}),
        "delete_mcp_server": lambda: app.delete_mcp_server(
            data["server_id"]),
        "test_mcp_connection": lambda: app.test_mcp_connection(
            data["server_id"]),
        # Expert definitions
        "save_expert_definition": lambda: app.save_expert_definition(
            data["entity_id"], data["mcp_server_id"],
            data["tool_name"], data.get("description", ""),
            data.get("default_arguments"),
            data.get("query_param_name", "query"),
            data.get("timeout_seconds", 300)),
        "get_expert_definitions": lambda: app.get_expert_definitions(),
        "consult_expert": lambda: app.consult_expert(
            data["expert_name"], data["query"]),
    }

    handler = handlers.get(method)
    if not handler:
        return web.json_response(
            {"error": f"Unknown method: {method}"}, status=404,
        )

    try:
        result: object = handler()
        if inspect.isawaitable(result):
            result = await result
        resp = web.json_response(
            {"result": result, "state": app.get_state()},
        )
        return set_session_cookie(ctx, resp, sid)
    except Exception as e:
        logger.exception("API error: %s", method)
        return web.json_response({"error": str(e)}, status=500)
    finally:
        # Clear per-request keys after use
        if api_keys:
            app.clear_request_api_keys()
