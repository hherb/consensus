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
from .auth import AuthDatabase, AuthManager, get_available_oauth_providers, build_oauth_authorize_url
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

# Auth cookie/header name
AUTH_COOKIE = "consensus_auth"
AUTH_HEADER = "Authorization"

# Paths that don't require authentication (even in multi-user mode)
AUTH_EXEMPT_PATHS = frozenset({
    "/health",
    "/auth/register",
    "/auth/login",
    "/auth/oauth/providers",
    "/auth/status",
})


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

    if multi_user and not allowed_origins:
        logger.warning(
            "Multi-user mode with no CONSENSUS_ALLOWED_ORIGINS set. "
            "CORS will reject all cross-origin requests. Set "
            "CONSENSUS_ALLOWED_ORIGINS=https://yourdomain.com to allow access."
        )

    # ------------------------------------------------------------------
    # Authentication setup (multi-user mode only)
    # ------------------------------------------------------------------
    auth_mgr: AuthManager | None = None
    auth_required = multi_user  # auth only enforced in hosted/multi-user mode

    if multi_user:
        from .config import get_data_dir
        auth_db_path = os.path.join(get_data_dir(), "auth.db")
        auth_db = AuthDatabase(auth_db_path)
        auth_mgr = AuthManager(auth_db)

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    @web.middleware
    async def security_headers_middleware(request: web.Request,
                                         handler: RequestHandler) -> web.StreamResponse:
        """Add security headers to all responses."""
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @web.middleware
    async def cors_middleware(request: web.Request,
                              handler: RequestHandler) -> web.StreamResponse:
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
                else:
                    # Block: unknown origin in any mode
                    return web.json_response(
                        {"error": "Forbidden origin"}, status=403,
                    )
        response = await handler(request)
        return response

    @web.middleware
    async def rate_limit_middleware(request: web.Request,
                                   handler: RequestHandler) -> web.StreamResponse:
        """Simple per-IP rate limiting for API endpoints."""
        if not request.path.startswith("/api/"):
            return await handler(request)

        # Use session ID if available, fall back to IP
        client_key = request.cookies.get(SESSION_COOKIE, "")
        if not client_key:
            transport = request.transport
            peername = transport.get_extra_info("peername") if transport else None
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

    @web.middleware
    async def auth_middleware(request: web.Request,
                              handler: RequestHandler) -> web.StreamResponse:
        """Enforce authentication in multi-user mode.

        Extracts auth token from cookie or Authorization header and attaches
        the authenticated user to request['auth_user']. Unauthenticated
        requests to protected paths get a 401 response.
        """
        request["auth_user"] = None

        if not auth_required or not auth_mgr:
            return await handler(request)

        # Allow auth endpoints, static files, health checks, and OAuth callbacks
        path = request.path
        if (path in AUTH_EXEMPT_PATHS
                or path.startswith("/auth/oauth/")
                or not path.startswith("/api/")):
            return await handler(request)

        # Extract token from Authorization header or cookie
        token = ""
        auth_header = request.headers.get(AUTH_HEADER, "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get(AUTH_COOKIE, "")

        if token:
            user = auth_mgr.get_current_user(token)
            if user:
                request["auth_user"] = user
                return await handler(request)

        return web.json_response(
            {"error": "Authentication required"},
            status=401,
        )

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
        assert session_mgr is not None  # guaranteed when shared_app is None
        sid = request.cookies.get(SESSION_COOKIE, "")
        if not sid or not session_mgr.is_valid_session_id(sid):
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

    def _extract_api_keys(request: web.Request) -> dict[str, str]:
        """Extract per-provider API keys from the X-API-Keys header.

        Returns a dict mapping provider_id (as str) to key value.
        Keys are sent via header only — never in the request body.
        """
        header = request.headers.get("X-API-Keys", "")
        if not header:
            return {}
        try:
            header_keys = json.loads(header)
            if isinstance(header_keys, dict):
                return {str(k): v for k, v in header_keys.items()}
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    # ------------------------------------------------------------------
    # API handler
    # ------------------------------------------------------------------

    async def handle_api(request: web.Request) -> web.StreamResponse:
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
        api_keys = _extract_api_keys(request)
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
            return _set_session_cookie(resp, sid)
        except Exception as e:
            logger.exception("API error: %s", method)
            return web.json_response({"error": str(e)}, status=500)
        finally:
            # Clear per-request keys after use
            if api_keys:
                app.clear_request_api_keys()

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    async def handle_auth_register(request: web.Request) -> web.StreamResponse:
        """Register a new user with email/password."""
        if not auth_mgr:
            return web.json_response(
                {"error": "Authentication not enabled"}, status=404,
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        try:
            user, token = auth_mgr.register(
                email=data.get("email", ""),
                password=data.get("password", ""),
                display_name=data.get("display_name", ""),
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        resp = web.json_response({"user": user.to_dict(), "token": token})
        resp.set_cookie(
            AUTH_COOKIE, token,
            max_age=86400 * 30, httponly=True,
            samesite="Lax", secure=bool(allowed_origins),
        )
        return resp

    async def handle_auth_login(request: web.Request) -> web.StreamResponse:
        """Authenticate with email/password."""
        if not auth_mgr:
            return web.json_response(
                {"error": "Authentication not enabled"}, status=404,
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        try:
            user, token = auth_mgr.login(
                email=data.get("email", ""),
                password=data.get("password", ""),
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)

        resp = web.json_response({"user": user.to_dict(), "token": token})
        resp.set_cookie(
            AUTH_COOKIE, token,
            max_age=86400 * 30, httponly=True,
            samesite="Lax", secure=bool(allowed_origins),
        )
        return resp

    async def handle_auth_logout(request: web.Request) -> web.StreamResponse:
        """Revoke the current auth token."""
        if not auth_mgr:
            return web.json_response({"ok": True})

        token = ""
        auth_header = request.headers.get(AUTH_HEADER, "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get(AUTH_COOKIE, "")

        if token:
            auth_mgr.logout(token)

        resp = web.json_response({"ok": True})
        resp.del_cookie(AUTH_COOKIE)
        return resp

    async def handle_auth_status(request: web.Request) -> web.StreamResponse:
        """Check current auth status. Returns user info if authenticated."""
        if not auth_mgr:
            # Auth not enabled: report as not required
            return web.json_response({
                "auth_required": False,
                "authenticated": False,
                "user": None,
            })

        token = ""
        auth_header = request.headers.get(AUTH_HEADER, "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get(AUTH_COOKIE, "")

        user = auth_mgr.get_current_user(token) if token else None
        return web.json_response({
            "auth_required": True,
            "authenticated": user is not None,
            "user": user.to_dict() if user else None,
            "oauth_providers": get_available_oauth_providers(),
        })

    async def handle_auth_me(request: web.Request) -> web.StreamResponse:
        """Get current user profile (requires auth)."""
        user = request.get("auth_user")
        if not user:
            return web.json_response({"error": "Not authenticated"}, status=401)
        return web.json_response({"user": user.to_dict()})

    async def handle_auth_update_profile(request: web.Request) -> web.StreamResponse:
        """Update the current user's profile."""
        if not auth_mgr:
            return web.json_response({"error": "Auth not enabled"}, status=404)

        user = request.get("auth_user")
        if not user:
            return web.json_response({"error": "Not authenticated"}, status=401)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        auth_mgr.db.update_user(user.id, **data)
        updated = auth_mgr.db.get_user_by_id(user.id)
        return web.json_response({"user": updated.to_dict() if updated else None})

    # -- OAuth endpoints ---------------------------------------------------

    async def handle_oauth_providers(request: web.Request) -> web.StreamResponse:
        """List available OAuth providers."""
        return web.json_response({
            "providers": get_available_oauth_providers(),
        })

    async def handle_oauth_authorize(request: web.Request) -> web.StreamResponse:
        """Initiate OAuth flow — redirect user to provider."""
        if not auth_mgr:
            return web.json_response({"error": "Auth not enabled"}, status=404)

        provider = request.match_info["provider"]
        # Build redirect URI from request
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        host_header = request.headers.get("X-Forwarded-Host", request.host)
        redirect_uri = f"{scheme}://{host_header}/auth/oauth/callback/{provider}"

        state = secrets.token_urlsafe(32)
        auth_mgr.db.store_oauth_state(state, provider, redirect_uri)

        url = build_oauth_authorize_url(provider, redirect_uri, state)
        if not url:
            return web.json_response(
                {"error": f"OAuth provider '{provider}' not configured"},
                status=400,
            )

        raise web.HTTPFound(location=url)

    async def handle_oauth_callback(request: web.Request) -> web.StreamResponse:
        """Handle OAuth callback from provider."""
        if not auth_mgr:
            return web.json_response({"error": "Auth not enabled"}, status=404)

        provider = request.match_info["provider"]

        # Get code and state from query params (GET) or form body (POST, e.g. Apple)
        if request.method == "POST":
            post_data = await request.post()
            code = post_data.get("code", "")
            state = post_data.get("state", "")
        else:
            code = request.query.get("code", "")
            state = request.query.get("state", "")

        if not code or not state:
            error = request.query.get("error", "unknown_error")
            return web.Response(
                status=400,
                content_type="text/html",
                text=f"<html><body><h2>OAuth Error</h2><p>{error}</p>"
                     f"<p><a href='/'>Return to app</a></p></body></html>",
            )

        # Validate state
        state_info = auth_mgr.db.consume_oauth_state(state)
        if not state_info or state_info["provider"] != provider:
            return web.Response(
                status=400,
                content_type="text/html",
                text="<html><body><h2>Invalid OAuth State</h2>"
                     "<p>The OAuth session has expired. Please try again.</p>"
                     "<p><a href='/'>Return to app</a></p></body></html>",
            )

        redirect_uri = state_info.get("redirect_uri", "")
        try:
            user, token = await auth_mgr.oauth_callback(
                provider, code, redirect_uri,
            )
        except ValueError as e:
            return web.Response(
                status=400,
                content_type="text/html",
                text=f"<html><body><h2>OAuth Error</h2><p>{e}</p>"
                     f"<p><a href='/'>Return to app</a></p></body></html>",
            )

        # Redirect to app with auth cookie set
        resp = web.HTTPFound(location="/")
        resp.set_cookie(
            AUTH_COOKIE, token,
            max_age=86400 * 30, httponly=True,
            samesite="Lax", secure=bool(allowed_origins),
        )
        return resp

    # ------------------------------------------------------------------
    # Health endpoint
    # ------------------------------------------------------------------

    async def handle_health(request: web.Request) -> web.StreamResponse:
        """Health check endpoint for load balancers."""
        info: dict = {"status": "ok"}
        if session_mgr:
            info["active_sessions"] = session_mgr.active_count
        return web.json_response(info)

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    async def serve_static(request: web.Request) -> web.StreamResponse:
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

    middlewares = [
        security_headers_middleware, cors_middleware,
        rate_limit_middleware, auth_middleware,
    ]
    webapp = web.Application(middlewares=middlewares)

    # Health
    webapp.router.add_get("/health", handle_health)

    # Auth routes
    webapp.router.add_post("/auth/register", handle_auth_register)
    webapp.router.add_post("/auth/login", handle_auth_login)
    webapp.router.add_post("/auth/logout", handle_auth_logout)
    webapp.router.add_get("/auth/status", handle_auth_status)
    webapp.router.add_get("/auth/me", handle_auth_me)
    webapp.router.add_post("/auth/me", handle_auth_update_profile)
    webapp.router.add_get("/auth/oauth/providers", handle_oauth_providers)
    webapp.router.add_get("/auth/oauth/authorize/{provider}", handle_oauth_authorize)
    webapp.router.add_get("/auth/oauth/callback/{provider}", handle_oauth_callback)
    webapp.router.add_post("/auth/oauth/callback/{provider}", handle_oauth_callback)

    # API
    webapp.router.add_post("/api/{method}", handle_api)

    # Static / SPA
    async def serve_index(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(os.path.join(static_dir, "index.html"))

    webapp.router.add_get("/", serve_index)
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
