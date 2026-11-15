"""The middleware stack: security headers, CORS, rate limit, CSRF, auth."""

import time

from aiohttp import web

from .constants import (
    CSRF_HEADER, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, RequestHandler,
)
from .context import CTX_KEY, extract_auth_token, prune_stale


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
    ctx = request.app[CTX_KEY]
    if request.path.startswith("/api/"):
        origin = request.headers.get("Origin", "")
        if origin:
            # In single-user mode, allow the local origin
            local_origin = f"http://{ctx.host}:{ctx.port}"
            if origin == local_origin:
                pass  # always allowed
            elif ctx.allowed_origins and origin in ctx.allowed_origins:
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
    ctx = request.app[CTX_KEY]
    if not request.path.startswith("/api/"):
        return await handler(request)

    # Key on the peer IP only.  A client-supplied cookie must never be
    # the rate-limit key: an unauthenticated caller could otherwise rotate
    # the cookie to get a fresh bucket per request and bypass the limit.
    transport = request.transport
    peername = transport.get_extra_info("peername") if transport else None
    client_key = peername[0] if peername else "unknown"

    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Periodically purge stale client entries (every RATE_LIMIT_WINDOW seconds)
    if now - ctx.rate_limit_last_cleanup > RATE_LIMIT_WINDOW:
        prune_stale(ctx.rate_limits, window_start)
        ctx.rate_limit_last_cleanup = now

    # Clean old entries and check limit
    if client_key not in ctx.rate_limits:
        ctx.rate_limits[client_key] = []
    timestamps = ctx.rate_limits[client_key]
    ctx.rate_limits[client_key] = [t for t in timestamps if t > window_start]

    if len(ctx.rate_limits[client_key]) >= RATE_LIMIT_MAX:
        return web.json_response(
            {"error": "Rate limit exceeded. Please wait before retrying."},
            status=429,
        )

    ctx.rate_limits[client_key].append(now)
    return await handler(request)


@web.middleware
async def csrf_middleware(request: web.Request,
                          handler: RequestHandler) -> web.StreamResponse:
    """Reject POSTs to /api/ and /auth/ a cross-site form could send.

    A plain HTML form can send neither ``Content-Type: application/json``
    nor a custom header, so a POST must have one of them
    (``CSRF_HEADER`` is how multipart uploads qualify). OAuth callbacks
    (form_post from Apple) are excluded.
    """
    if request.method == "POST" and (
        request.path.startswith("/api/")
        or request.path.startswith("/auth/")
    ):
        # Exclude OAuth callbacks which may use form_post (e.g. Apple)
        if not request.path.startswith("/auth/oauth/callback/"):
            content_type = request.content_type or ""
            if ("application/json" not in content_type
                    and CSRF_HEADER not in request.headers):
                return web.json_response(
                    {"error": "Content-Type must be application/json "
                              f"(or send the {CSRF_HEADER} header)"},
                    status=415,
                )
    return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request,
                          handler: RequestHandler) -> web.StreamResponse:
    """Enforce authentication in multi-user mode.

    Extracts auth token from cookie or Authorization header and attaches
    the authenticated user to request['auth_user']. Unauthenticated
    requests to protected paths get a 401 response.
    """
    ctx = request.app[CTX_KEY]
    request["auth_user"] = None

    if not ctx.auth_required or not ctx.auth_mgr:
        return await handler(request)

    # Only /api/* routes require authentication; everything else
    # (static files, /auth/*, /health, OAuth callbacks) is exempt.
    # /auth/me checks request["auth_user"] itself, so resolve it there.
    if request.path == "/auth/me":
        token = extract_auth_token(request)
        if token:
            request["auth_user"] = ctx.auth_mgr.get_current_user(token)
        return await handler(request)
    if not request.path.startswith("/api/"):
        return await handler(request)

    token = extract_auth_token(request)
    if token:
        user = ctx.auth_mgr.get_current_user(token)
        if user:
            request["auth_user"] = user
            return await handler(request)

    return web.json_response(
        {"error": "Authentication required"},
        status=401,
    )




#: Outermost first, as ``web.Application`` applies them.
MIDDLEWARES = [
    security_headers_middleware, cors_middleware,
    rate_limit_middleware, csrf_middleware, auth_middleware,
]
