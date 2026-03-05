"""Web server mode using aiohttp."""

import asyncio
import inspect
import json
import logging
import os
import secrets
import time
from typing import Awaitable, Callable

from aiohttp import web

# Type alias for aiohttp request handlers
RequestHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]

from .app import ConsensusApp
from .session import SessionManager

logger = logging.getLogger(__name__)

# Default server settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

# Rate limiting defaults
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 120  # requests per window

# Session cookie name
SESSION_COOKIE = "consensus_sid"


def _generate_session_id() -> str:
    """Generate a cryptographically random session ID."""
    return secrets.token_urlsafe(32)


async def launch_web(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                     multi_user: bool = False) -> None:
    """Start the aiohttp web server and block until interrupted.

    Args:
        host: Bind address.
        port: Bind port.
        multi_user: If True, each browser session gets its own ConsensusApp
                    instance with per-session SQLite. If False (default),
                    all clients share a single ConsensusApp (local/desktop use).
    """
    from .config import load_env
    load_env()

    static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))

    # Single-user mode: one shared app (preserves original behavior)
    shared_app: ConsensusApp | None = None
    session_mgr: SessionManager | None = None

    if multi_user:
        session_mgr = SessionManager()
    else:
        shared_app = ConsensusApp()

    # Rate limiting state: {client_key: [timestamps]}
    rate_limits: dict[str, list[float]] = {}
    rate_limit_last_cleanup: float = 0.0

    # Allowed CORS origins (configurable via env)
    allowed_origins_env = os.environ.get("CONSENSUS_ALLOWED_ORIGINS", "")
    allowed_origins: set[str] = set()
    if allowed_origins_env:
        allowed_origins = {o.strip() for o in allowed_origins_env.split(",") if o.strip()}

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    @web.middleware
    async def security_headers_middleware(request: web.Request,
                                         handler: RequestHandler) -> web.Response:
        """Add security headers to all responses."""
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @web.middleware
    async def cors_middleware(request: web.Request,
                              handler: RequestHandler) -> web.Response:
        """Restrict API access to same-origin or allowed-origin requests."""
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            if origin:
                # In single-user mode, allow the local origin
                local_origin = f"http://{host}:{port}"
                if origin == local_origin:
                    pass  # always allowed
                elif allowed_origins and origin in allowed_origins:
                    pass  # explicitly allowed
                elif not allowed_origins and not multi_user:
                    # Single-user: only local origin
                    return web.json_response(
                        {"error": "Forbidden origin"}, status=403,
                    )
                elif allowed_origins and origin not in allowed_origins:
                    return web.json_response(
                        {"error": "Forbidden origin"}, status=403,
                    )
                # In multi-user mode with no explicit origins, allow any
                # (deployment should use reverse proxy for origin control)
        response = await handler(request)
        return response

    @web.middleware
    async def rate_limit_middleware(request: web.Request,
                                   handler: RequestHandler) -> web.Response:
        """Simple per-IP rate limiting for API endpoints."""
        if not request.path.startswith("/api/"):
            return await handler(request)

        # Use session ID if available, fall back to IP
        client_key = request.cookies.get(SESSION_COOKIE, "")
        if not client_key:
            peername = request.transport.get_extra_info("peername")
            client_key = peername[0] if peername else "unknown"

        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW

        # Periodically purge stale client entries (every RATE_LIMIT_WINDOW seconds)
        nonlocal rate_limit_last_cleanup
        if now - rate_limit_last_cleanup > RATE_LIMIT_WINDOW:
            stale_keys = [
                k for k, ts in rate_limits.items()
                if not ts or ts[-1] < window_start
            ]
            for k in stale_keys:
                del rate_limits[k]
            rate_limit_last_cleanup = now

        # Clean old entries and check limit
        if client_key not in rate_limits:
            rate_limits[client_key] = []
        timestamps = rate_limits[client_key]
        rate_limits[client_key] = [t for t in timestamps if t > window_start]

        if len(rate_limits[client_key]) >= RATE_LIMIT_MAX:
            return web.json_response(
                {"error": "Rate limit exceeded. Please wait before retrying."},
                status=429,
            )

        rate_limits[client_key].append(now)
        return await handler(request)

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    async def _get_app_for_request(request: web.Request) -> tuple[ConsensusApp | None, str]:
        """Get the ConsensusApp for this request.

        Returns (app, session_id). In single-user mode, session_id is empty.
        """
        if shared_app:
            return shared_app, ""

        # Multi-user: look up or create session
        sid = request.cookies.get(SESSION_COOKIE, "")
        if not sid:
            sid = _generate_session_id()

        app = await session_mgr.get_app(sid)
        if app is None:
            return None, sid
        return app, sid

    def _set_session_cookie(response: web.Response, sid: str) -> web.Response:
        """Set the session cookie on a response if in multi-user mode."""
        if sid and session_mgr:
            response.set_cookie(
                SESSION_COOKIE, sid,
                max_age=session_mgr.session_ttl,
                httponly=True,
                samesite="Lax",
                secure=bool(allowed_origins),  # secure if served via HTTPS
            )
        return response

    # ------------------------------------------------------------------
    # BYOK: extract API keys from request
    # ------------------------------------------------------------------

    def _extract_api_keys(request: web.Request, data: dict) -> dict[str, str]:
        """Extract per-provider API keys from the request.

        Keys can be provided:
        1. In the JSON body as `_api_keys`: {provider_id: key, ...}
        2. In the header `X-API-Keys` as JSON: {provider_id: key, ...}

        Returns a dict mapping provider_id (as str) to key value.
        """
        keys: dict[str, str] = {}

        # From JSON body
        body_keys = data.pop("_api_keys", None)
        if isinstance(body_keys, dict):
            keys.update({str(k): v for k, v in body_keys.items()})

        # From header (takes precedence)
        header = request.headers.get("X-API-Keys", "")
        if header:
            try:
                header_keys = json.loads(header)
                if isinstance(header_keys, dict):
                    keys.update({str(k): v for k, v in header_keys.items()})
            except (json.JSONDecodeError, TypeError):
                pass

        return keys

    # ------------------------------------------------------------------
    # API handler
    # ------------------------------------------------------------------

    async def handle_api(request: web.Request) -> web.Response:
        """Route API calls to the appropriate ConsensusApp method."""
        method = request.match_info["method"]

        app, sid = await _get_app_for_request(request)
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
        api_keys = _extract_api_keys(request, data)
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
            "pause_discussion": lambda: app.pause_discussion(),
            "resume_discussion": lambda: app.resume_discussion(),
            "reopen_discussion": lambda: app.reopen_discussion(),
            # Export
            "get_export_data": lambda: app.get_export_data(
                data["discussion_id"]),
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
            resp = web.json_response(
                {"result": result, "state": app.get_state()},
            )
            return _set_session_cookie(resp, sid)
        except Exception as e:
            logger.exception("API error: %s", method)
            return web.json_response({"error": str(e)}, status=500)
        finally:
            # Clear per-request keys after use
            if api_keys:
                app.clear_request_api_keys()

    # ------------------------------------------------------------------
    # Health endpoint
    # ------------------------------------------------------------------

    async def handle_health(request: web.Request) -> web.Response:
        """Health check endpoint for load balancers."""
        info: dict = {"status": "ok"}
        if session_mgr:
            info["active_sessions"] = session_mgr.active_count
        return web.json_response(info)

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # App setup
    # ------------------------------------------------------------------

    middlewares = [security_headers_middleware, cors_middleware, rate_limit_middleware]
    webapp = web.Application(middlewares=middlewares)
    webapp.router.add_get("/health", handle_health)
    webapp.router.add_post("/api/{method}", handle_api)
    webapp.router.add_get(
        "/", lambda r: web.FileResponse(
            os.path.join(static_dir, "index.html")),
    )
    webapp.router.add_get("/{path:.*}", serve_static)

    if session_mgr:
        session_mgr.start_cleanup_loop()

    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    mode = "multi-user" if multi_user else "single-user"
    print(f"Consensus web server running at http://{host}:{port} ({mode} mode)")
    try:
        await asyncio.Event().wait()
    finally:
        if session_mgr:
            await session_mgr.stop()
        await runner.cleanup()
