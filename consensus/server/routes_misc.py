"""Health, the SSE event stream, and the static frontend."""

import asyncio
import json
import os

from aiohttp import web

from .constants import SESSION_COOKIE, SSE_KEEPALIVE_SECONDS
from .context import CTX_KEY, session_app


async def handle_health(request: web.Request) -> web.StreamResponse:
    """Health check endpoint for load balancers."""
    ctx = request.app[CTX_KEY]
    info: dict = {"status": "ok"}
    if ctx.session_mgr:
        info["active_sessions"] = ctx.session_mgr.active_count
    return web.json_response(info)


async def handle_events(request: web.Request) -> web.StreamResponse:
    """SSE endpoint for real-time events (tool progress, etc.).

    In ``--multi-user`` the stream ends once its session is no longer live
    (#135): the app it listens to was shut down. The reconnect gets the
    no-session 400, and the frontend rebinds after a dispatch call has made
    the next session.
    """
    app_instance = await session_app(request)
    if isinstance(app_instance, web.Response):
        return app_instance
    session_mgr = request.app[CTX_KEY].session_mgr
    sid = request.cookies.get(SESSION_COOKIE, "")

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    await resp.prepare(request)

    queue: asyncio.Queue = asyncio.Queue()

    def on_tool_progress(data: dict) -> None:
        queue.put_nowait(("tool_progress", data))

    def on_user_input_request(data: dict) -> None:
        queue.put_nowait(("user_input_request", data))

    app_instance.on("tool_progress", on_tool_progress)
    app_instance.on("user_input_request", on_user_input_request)
    try:
        while True:
            try:
                event_type, data = await asyncio.wait_for(
                    queue.get(), timeout=SSE_KEEPALIVE_SECONDS,
                )
                event_str = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                await resp.write(event_str.encode())
            except asyncio.TimeoutError:
                # touch=False: an open tab alone must not keep a session alive.
                if (session_mgr is not None
                        and session_mgr.lookup(sid, touch=False)
                        is not app_instance):
                    break
                await resp.write(b": keepalive\n\n")
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        app_instance.off("tool_progress", on_tool_progress)
        app_instance.off("user_input_request", on_user_input_request)

    return resp


async def serve_static(request: web.Request) -> web.StreamResponse:
    """Serve static files with path traversal protection."""
    ctx = request.app[CTX_KEY]
    path = request.match_info.get("path", "") or "index.html"
    filepath = os.path.realpath(os.path.join(ctx.static_dir, path))
    # Prevent path traversal outside the static directory
    if not filepath.startswith(ctx.static_dir + os.sep) and filepath != ctx.static_dir:
        return web.Response(status=403, text="Forbidden")
    if os.path.isfile(filepath):
        return web.FileResponse(filepath)
    return web.FileResponse(os.path.join(ctx.static_dir, "index.html"))


async def serve_index(request: web.Request) -> web.StreamResponse:
    """Serve the single-page app's ``index.html`` for ``/``."""
    ctx = request.app[CTX_KEY]
    return web.FileResponse(os.path.join(ctx.static_dir, "index.html"))
