"""``/auth/*``: password sign-in, the profile, and the OAuth flow."""

import asyncio
import html as html_mod
import logging
import os
import secrets
import time
from typing import Callable, TypeVar

from aiohttp import web

from ..auth import (
    AUTH_TOKEN_TTL,
    build_oauth_authorize_url,
    get_available_oauth_providers,
)
from .constants import AUTH_COOKIE, LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW
from .context import CTX_KEY, extract_auth_token, prune_stale
from .request_io import non_string_field, read_json_object

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def _hash_off_loop(request: web.Request, fn: Callable[..., _T],
                         **kwargs: object) -> _T:
    """Run a PBKDF2-bound auth call in a worker thread (#141).

    One hash takes ~0.13 s; on the event loop a burst of logins stalled
    every other request, SSE included. ``password_slots`` bounds how many
    run at once.
    """
    async with request.app[CTX_KEY].password_slots:
        return await asyncio.to_thread(fn, **kwargs)


async def handle_auth_register(request: web.Request) -> web.StreamResponse:
    """Register a new user with email/password."""
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        return web.json_response(
            {"error": "Authentication not enabled"}, status=404,
        )
    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    bad = non_string_field(data, ("email", "password", "display_name"))
    if bad:
        return bad

    try:
        user, token = await _hash_off_loop(
            request, ctx.auth_mgr.register,
            email=data.get("email", ""),
            password=data.get("password", ""),
            display_name=data.get("display_name", ""),
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)

    resp = web.json_response({"user": user.to_dict()})
    resp.set_cookie(
        AUTH_COOKIE, token,
        max_age=AUTH_TOKEN_TTL, httponly=True,
        samesite="Lax", secure=bool(ctx.allowed_origins),
    )
    return resp


async def handle_auth_login(request: web.Request) -> web.StreamResponse:
    """Authenticate with email/password."""
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        return web.json_response(
            {"error": "Authentication not enabled"}, status=404,
        )
    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    bad = non_string_field(data, ("email", "password"))
    if bad:
        return bad
    email = data.get("email", "").strip().lower()

    # Per-email brute-force protection
    now = time.time()
    window_start = now - LOGIN_RATE_LIMIT_WINDOW
    if now - ctx.login_attempts_last_cleanup > LOGIN_RATE_LIMIT_WINDOW:
        prune_stale(ctx.login_attempts, window_start)
        ctx.login_attempts_last_cleanup = now
    if email:
        attempts = ctx.login_attempts.get(email, [])
        attempts = [t for t in attempts if t > window_start]
        ctx.login_attempts[email] = attempts
        if len(attempts) >= LOGIN_RATE_LIMIT_MAX:
            return web.json_response(
                {"error": "Too many login attempts. Please try again later."},
                status=429,
            )
        # Count the attempt before the await below, or a concurrent burst
        # would all pass the check; a success takes it back.
        attempts.append(now)

    try:
        user, token = await _hash_off_loop(
            request, ctx.auth_mgr.login,
            email=email,
            password=data.get("password", ""),
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=401)
    if email:
        # Another request may have replaced or pruned the list meanwhile.
        current = ctx.login_attempts.get(email)
        if current and now in current:
            current.remove(now)

    resp = web.json_response({"user": user.to_dict()})
    resp.set_cookie(
        AUTH_COOKIE, token,
        max_age=AUTH_TOKEN_TTL, httponly=True,
        samesite="Lax", secure=bool(ctx.allowed_origins),
    )
    return resp


async def handle_auth_logout(request: web.Request) -> web.StreamResponse:
    """Revoke the current auth token and always clear the auth cookie."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_mgr:
        token = extract_auth_token(request)
        if token:
            try:
                ctx.auth_mgr.logout(token)
            except Exception:
                # The cookie still goes: a 500 here left the browser signed
                # in while the UI showed it signed out.
                logger.exception("Could not revoke the auth token on logout")

    resp = web.json_response({"ok": True})
    resp.del_cookie(AUTH_COOKIE)
    return resp


async def handle_auth_status(request: web.Request) -> web.StreamResponse:
    """Check current auth status. Returns user info if authenticated."""
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        # Auth not enabled: report as not required
        return web.json_response({
            "auth_required": False,
            "authenticated": False,
            "user": None,
        })

    token = extract_auth_token(request)
    user = ctx.auth_mgr.get_current_user(token) if token else None
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
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        return web.json_response({"error": "Auth not enabled"}, status=404)

    user = request.get("auth_user")
    if not user:
        return web.json_response({"error": "Not authenticated"}, status=401)

    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    bad = non_string_field(data, ("display_name", "avatar_url"))
    if bad:
        return bad
    update_fields = {}
    for field in ("display_name", "avatar_url"):
        if field in data:
            update_fields[field] = data[field]
    if update_fields:
        ctx.auth_mgr.db.update_user(user.id, **update_fields)
    updated = ctx.auth_mgr.db.get_user_by_id(user.id)
    return web.json_response({"user": updated.to_dict() if updated else None})


async def handle_oauth_providers(request: web.Request) -> web.StreamResponse:
    """List available OAuth providers."""
    return web.json_response({
        "providers": get_available_oauth_providers(),
    })


async def handle_oauth_authorize(request: web.Request) -> web.StreamResponse:
    """Initiate OAuth flow — redirect user to provider."""
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        return web.json_response({"error": "Auth not enabled"}, status=404)

    provider = request.match_info["provider"]

    # Build redirect URI from CONSENSUS_BASE_URL (required for OAuth)
    # to prevent host header injection attacks.
    base_url = os.environ.get("CONSENSUS_BASE_URL", "").rstrip("/")
    if not base_url:
        base_url = f"{request.scheme}://{request.host}"
        logger.warning(
            "CONSENSUS_BASE_URL not set; falling back to request host "
            "for OAuth redirect. Set CONSENSUS_BASE_URL for production."
        )
    redirect_uri = f"{base_url}/auth/oauth/callback/{provider}"

    state = secrets.token_urlsafe(32)
    url = build_oauth_authorize_url(provider, redirect_uri, state)
    if not url:
        return web.json_response(
            {"error": f"OAuth provider '{provider}' not configured"},
            status=400,
        )

    # Only persist state after confirming provider is valid
    ctx.auth_mgr.db.store_oauth_state(state, provider, redirect_uri)
    raise web.HTTPFound(location=url)


async def handle_oauth_callback(request: web.Request) -> web.StreamResponse:
    """Handle OAuth callback from provider."""
    ctx = request.app[CTX_KEY]
    if not ctx.auth_mgr:
        return web.json_response({"error": "Auth not enabled"}, status=404)

    provider = request.match_info["provider"]

    # Get code and state from query params (GET) or form body (POST, e.g. Apple)
    if request.method == "POST":
        params = await request.post()
    else:
        params = request.query
    code = str(params.get("code", ""))
    state = str(params.get("state", ""))

    if not code or not state:
        error = html_mod.escape(str(params.get("error", "unknown_error")))
        return web.Response(
            status=400,
            content_type="text/html",
            text=f"<html><body><h2>OAuth Error</h2><p>{error}</p>"
                 f"<p><a href='/'>Return to app</a></p></body></html>",
        )

    # Validate state
    state_info = ctx.auth_mgr.db.consume_oauth_state(state)
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
        user, token = await ctx.auth_mgr.oauth_callback(
            provider, code, redirect_uri,
        )
    except ValueError as e:
        safe_msg = html_mod.escape(str(e))
        return web.Response(
            status=400,
            content_type="text/html",
            text=f"<html><body><h2>OAuth Error</h2><p>{safe_msg}</p>"
                 f"<p><a href='/'>Return to app</a></p></body></html>",
        )

    # Redirect to app with auth cookie set
    resp = web.HTTPFound(location="/")
    resp.set_cookie(
        AUTH_COOKIE, token,
        max_age=AUTH_TOKEN_TTL, httponly=True,
        samesite="Lax", secure=bool(ctx.allowed_origins),
    )
    return resp
