"""Web server mode using aiohttp."""

import asyncio
import json
import os

from aiohttp import web

from .app import ConsensusApp


async def launch_web(host: str = "0.0.0.0", port: int = 8080):
    app = ConsensusApp()
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    async def handle_api(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        try:
            data = await request.json() if request.content_length else {}
        except json.JSONDecodeError:
            data = {}

        handlers = {
            "get_state": lambda: app.get_state(),
            "add_entity": lambda: app.add_entity(**data),
            "remove_entity": lambda: app.remove_entity(data["entity_id"]),
            "set_moderator": lambda: app.set_moderator(data["entity_id"]),
            "set_topic": lambda: app.set_topic(data["topic"]),
            "start_discussion": lambda: app.start_discussion(),
            "submit_human_message": lambda: app.submit_human_message(
                data["entity_id"], data["content"]
            ),
            "submit_moderator_message": lambda: app.submit_moderator_message(
                data["content"]
            ),
            "generate_ai_turn": lambda: app.generate_ai_turn(),
            "complete_turn": lambda: app.complete_turn(
                data.get("moderator_summary", "")
            ),
            "reassign_turn": lambda: app.reassign_turn(data["entity_id"]),
            "mediate": lambda: app.mediate(data.get("context", "")),
            "conclude": lambda: app.conclude_discussion(),
            "reset": lambda: app.reset(),
        }

        handler = handlers.get(method)
        if not handler:
            return web.json_response({"error": f"Unknown method: {method}"}, status=404)

        try:
            result = handler()
            if asyncio.iscoroutine(result):
                result = await result
            return web.json_response({"result": result, "state": app.get_state()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def serve_static(request: web.Request) -> web.Response:
        path = request.match_info.get("path", "") or "index.html"
        filepath = os.path.join(static_dir, path)
        if os.path.isfile(filepath):
            return web.FileResponse(filepath)
        return web.FileResponse(os.path.join(static_dir, "index.html"))

    webapp = web.Application()
    webapp.router.add_post("/api/{method}", handle_api)
    webapp.router.add_get("/", lambda r: web.FileResponse(os.path.join(static_dir, "index.html")))
    webapp.router.add_get("/{path:.*}", serve_static)

    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"Consensus web server running at http://{host}:{port}")
    await asyncio.Event().wait()
