"""Web server mode using aiohttp."""

import asyncio
import inspect
import json
import os

from aiohttp import web

from .app import ConsensusApp

# Default server settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


async def launch_web(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Start the aiohttp web server and block until interrupted."""
    app = ConsensusApp()
    static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))

    @web.middleware
    async def cors_middleware(request: web.Request,
                              handler: object) -> web.Response:
        """Restrict API access to same-origin requests."""
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            if origin:
                allowed = f"http://{host}:{port}"
                if origin != allowed:
                    return web.json_response(
                        {"error": "Forbidden origin"}, status=403,
                    )
        response = await handler(request)
        return response

    async def handle_api(request: web.Request) -> web.Response:
        """Route API calls to the appropriate ConsensusApp method."""
        method = request.match_info["method"]
        try:
            data = await request.json()
        except (json.JSONDecodeError, Exception):
            data = {}

        handlers = {
            # State
            "get_state": lambda: app.get_state(),
            # Providers
            "add_provider": lambda: app.add_provider(
                data["name"], data["base_url"], data.get("api_key_env", "")),
            "update_provider": lambda: app.update_provider(
                data["provider_id"], **{
                    k: v for k, v in data.items() if k != "provider_id"
                }),
            "delete_provider": lambda: app.delete_provider(
                data["provider_id"]),
            # Entity profiles
            "save_entity": lambda: app.save_entity(**data),
            "delete_entity": lambda: app.delete_entity(data["entity_id"]),
            # Prompts
            "save_prompt": lambda: app.save_prompt(**data),
            "delete_prompt": lambda: app.delete_prompt(data["prompt_id"]),
            # Discussion setup
            "add_to_discussion": lambda: app.add_to_discussion(
                data["entity_id"],
                data.get("is_moderator", False),
                data.get("also_participant", False)),
            "remove_from_discussion": lambda: app.remove_from_discussion(
                data["entity_id"]),
            "set_moderator": lambda: app.set_moderator(
                data["entity_id"], data.get("also_participant", False)),
            "set_topic": lambda: app.set_topic(data["topic"]),
            # Discussion lifecycle
            "start_discussion": lambda: app.start_discussion(
                data.get("moderator_participates", False)),
            "submit_human_message": lambda: app.submit_human_message(
                data["entity_id"], data["content"]),
            "submit_moderator_message": lambda: app.submit_moderator_message(
                data["content"]),
            "generate_ai_turn": lambda: app.generate_ai_turn(),
            "complete_turn": lambda: app.complete_turn(
                data.get("moderator_summary", "")),
            "reassign_turn": lambda: app.reassign_turn(data["entity_id"]),
            "mediate": lambda: app.mediate(data.get("context", "")),
            "conclude": lambda: app.conclude_discussion(),
            # History
            "load_discussion": lambda: app.load_discussion(
                data["discussion_id"]),
            "reset": lambda: app.reset(),
        }

        handler = handlers.get(method)
        if not handler:
            return web.json_response(
                {"error": f"Unknown method: {method}"}, status=404,
            )

        try:
            result = handler()
            if inspect.isawaitable(result):
                result = await result
            return web.json_response(
                {"result": result, "state": app.get_state()},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def serve_static(request: web.Request) -> web.Response:
        """Serve static files with path traversal protection."""
        path = request.match_info.get("path", "") or "index.html"
        filepath = os.path.realpath(os.path.join(static_dir, path))
        # Prevent path traversal outside the static directory
        if not filepath.startswith(static_dir + os.sep) and filepath != static_dir:
            return web.Response(status=403, text="Forbidden")
        if os.path.isfile(filepath):
            return web.FileResponse(filepath)
        return web.FileResponse(os.path.join(static_dir, "index.html"))

    webapp = web.Application(middlewares=[cors_middleware])
    webapp.router.add_post("/api/{method}", handle_api)
    webapp.router.add_get(
        "/", lambda r: web.FileResponse(
            os.path.join(static_dir, "index.html")),
    )
    webapp.router.add_get("/{path:.*}", serve_static)

    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"Consensus web server running at http://{host}:{port}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
