"""Per-server state and the request helpers every route shares.

``launch_web`` used to hold this state in closure variables shared by ~35
nested handlers. It now lives in one ``ServerContext``, stored on the aiohttp
application under ``CTX_KEY``; handlers are module-level functions that read
it with ``request.app[CTX_KEY]``.
"""

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field

from aiohttp import web

from ..app import ConsensusApp
from ..auth import AuthDatabase, AuthManager
from ..session import SessionManager
from .constants import (
    AUTH_COOKIE, AUTH_HEADER, NO_SESSION_ERROR, PASSWORD_HASH_CONCURRENCY,
    SESSION_COOKIE,
)

logger = logging.getLogger(__name__)


@dataclass
class ServerContext:
    """Everything the middleware and handlers of one server share.

    Attributes:
        host: Bind address (part of the always-allowed local CORS origin).
        port: Bind port (likewise).
        static_dir: Absolute path of the frontend's ``static/`` directory.
        shared_app: The one app in single-user mode, else ``None``.
        session_mgr: Per-session apps in ``--multi-user``, else ``None``.
        auth_mgr: Sign-in in ``--multi-user``, else ``None``.
        auth_required: Whether ``/api/`` demands a signed-in user.
        allowed_origins: Extra CORS origins (``CONSENSUS_ALLOWED_ORIGINS``);
            non-empty also marks cookies ``Secure``.
        rate_limits: Per-IP request timestamps, ``{client_key: [ts]}``.
        rate_limit_last_cleanup: When stale ``rate_limits`` were last purged.
        login_attempts: Failed logins per email, ``{email: [ts]}``. In-process
            only — does not apply across multiple workers/processes.
        login_attempts_last_cleanup: When stale ``login_attempts`` were last
            purged.
        password_slots: Bounds the password hashes running in worker
            threads at once (``PASSWORD_HASH_CONCURRENCY``, #141).
    """

    host: str
    port: int
    static_dir: str
    shared_app: ConsensusApp | None = None
    session_mgr: SessionManager | None = None
    auth_mgr: AuthManager | None = None
    auth_required: bool = False
    allowed_origins: set[str] = field(default_factory=set)
    rate_limits: dict[str, list[float]] = field(default_factory=dict)
    rate_limit_last_cleanup: float = 0.0
    login_attempts: dict[str, list[float]] = field(default_factory=dict)
    login_attempts_last_cleanup: float = 0.0
    password_slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(PASSWORD_HASH_CONCURRENCY))


CTX_KEY = web.AppKey("consensus_server_context", ServerContext)


def build_context(host: str, port: int, multi_user: bool) -> ServerContext:
    """Create the apps, auth and settings for a server (reads the env).

    Args:
        host: Bind address.
        port: Bind port.
        multi_user: One ``ConsensusApp`` per browser session (with sign-in)
            instead of one shared app.

    Returns:
        The populated ``ServerContext``.
    """
    # consensus/server/context.py -> consensus/static
    static_dir = os.path.realpath(os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "static"))

    # Single-user mode: one shared app
    shared_app: ConsensusApp | None = None
    session_mgr: SessionManager | None = None

    if multi_user:
        session_mgr = SessionManager()
    else:
        shared_app = ConsensusApp()

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

    # Authentication setup (multi-user mode only)
    auth_mgr: AuthManager | None = None
    if multi_user:
        from ..config import get_data_dir
        auth_db_path = os.path.join(get_data_dir(), "auth.db")
        auth_db = AuthDatabase(auth_db_path)
        auth_mgr = AuthManager(auth_db)

    return ServerContext(
        host=host, port=port, static_dir=static_dir,
        shared_app=shared_app, session_mgr=session_mgr, auth_mgr=auth_mgr,
        auth_required=multi_user,  # auth only enforced in multi-user mode
        allowed_origins=allowed_origins,
    )


def prune_stale(buckets: dict[str, list[float]], window_start: float) -> None:
    """Drop the keys whose newest timestamp is older than *window_start*.

    Keeps the per-IP and per-email limiter tables bounded by recent
    activity rather than by every client or email ever seen.
    """
    stale_keys = [k for k, ts in buckets.items()
                  if not ts or ts[-1] < window_start]
    for k in stale_keys:
        del buckets[k]


def _generate_session_id() -> str:
    """Generate a cryptographically random session ID."""
    return secrets.token_urlsafe(32)


async def get_app_for_request(request: web.Request) -> tuple[ConsensusApp | None, str]:
    """Get the ConsensusApp for this request.

    Returns (app, session_id). In single-user mode, session_id is empty;
    in ``--multi-user`` mode, app is ``None`` when the server is at capacity
    (the caller answers 503). Creates the session if need be, so only
    ``handle_api``, which sets the cookie on its reply, may call it (#132).
    """
    ctx = request.app[CTX_KEY]
    if ctx.shared_app:
        return ctx.shared_app, ""

    # Multi-user: look up or create session
    assert ctx.session_mgr is not None  # guaranteed when ctx.shared_app is None
    sid = request.cookies.get(SESSION_COOKIE, "")
    if not sid or not ctx.session_mgr.is_valid_session_id(sid):
        sid = _generate_session_id()

    app = await ctx.session_mgr.get_app(sid)
    if app is None:
        return None, sid
    return app, sid


async def session_app(request: web.Request) -> ConsensusApp | web.Response:
    """The request's ``ConsensusApp``, or the error response to send instead.

    The shared preamble of every ``/api/`` route outside the dispatch table:
    401 without a signed-in user where auth is required (``auth_middleware``
    already answers that; this keeps a route mounted elsewhere safe), 400
    when ``--multi-user`` has no live session for the cookie. These routes
    never create a session: they cannot set its cookie (#132).
    """
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"},
                                 status=401)
    if ctx.shared_app:
        return ctx.shared_app
    assert ctx.session_mgr is not None  # guaranteed when ctx.shared_app is None
    app = ctx.session_mgr.lookup(request.cookies.get(SESSION_COOKIE, ""))
    if app is None:
        return web.json_response({"error": NO_SESSION_ERROR}, status=400)
    return app


def set_session_cookie(ctx: ServerContext, response: web.Response,
                       sid: str) -> web.Response:
    """Set the session cookie on a response if in multi-user mode."""
    if sid and ctx.session_mgr:
        response.set_cookie(
            SESSION_COOKIE, sid,
            max_age=ctx.session_mgr.session_ttl,
            httponly=True,
            samesite="Lax",
            secure=bool(ctx.allowed_origins),  # secure if served via HTTPS
        )
    return response


def extract_auth_token(request: web.Request) -> str:
    """Extract auth token from Authorization header or cookie.

    Checks the Authorization header for a Bearer token first,
    then falls back to the auth cookie.
    """
    auth_header = request.headers.get(AUTH_HEADER, "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return request.cookies.get(AUTH_COOKIE, "")


def extract_api_keys(request: web.Request) -> dict[str, str]:
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
