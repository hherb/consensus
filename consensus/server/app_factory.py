"""Build the aiohttp application and run it (``launch_web``)."""

import asyncio
import logging

from aiohttp import web

from . import routes_auth, routes_documents, routes_images, routes_misc
from .constants import DEFAULT_HOST, DEFAULT_PORT
from .context import CTX_KEY, build_context
from .middleware import MIDDLEWARES
from .routes_api import handle_api

logger = logging.getLogger(__name__)


def create_web_app(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                   multi_user: bool = False) -> web.Application:
    """Build the aiohttp application: middleware, routes and lifecycle hooks.

    The session cleanup loop starts with the app and the shared app / session
    manager shut down with it (``on_startup`` / ``on_cleanup``), so a test
    client drives the same lifecycle as ``launch_web``.

    Args:
        host: Bind address (part of the always-allowed local CORS origin).
        port: Bind port (likewise).
        multi_user: If True, each browser session gets its own ConsensusApp
                    instance with per-session SQLite. If False (default),
                    all clients share a single ConsensusApp (local/desktop use).

    Returns:
        The configured, not yet started, ``web.Application``.
    """
    ctx = build_context(host, port, multi_user)
    webapp = web.Application(middlewares=MIDDLEWARES)
    webapp[CTX_KEY] = ctx

    # Health
    webapp.router.add_get("/health", routes_misc.handle_health)

    # Auth routes
    webapp.router.add_post("/auth/register", routes_auth.handle_auth_register)
    webapp.router.add_post("/auth/login", routes_auth.handle_auth_login)
    webapp.router.add_post("/auth/logout", routes_auth.handle_auth_logout)
    webapp.router.add_get("/auth/status", routes_auth.handle_auth_status)
    webapp.router.add_get("/auth/me", routes_auth.handle_auth_me)
    webapp.router.add_post("/auth/me", routes_auth.handle_auth_update_profile)
    webapp.router.add_get("/auth/oauth/providers", routes_auth.handle_oauth_providers)
    webapp.router.add_get("/auth/oauth/authorize/{provider}", routes_auth.handle_oauth_authorize)
    webapp.router.add_get("/auth/oauth/callback/{provider}", routes_auth.handle_oauth_callback)
    webapp.router.add_post("/auth/oauth/callback/{provider}", routes_auth.handle_oauth_callback)

    # Memory config
    webapp.router.add_get("/api/memory/config", routes_documents.handle_memory_config_get)
    webapp.router.add_put("/api/memory/config", routes_documents.handle_memory_config_put)
    webapp.router.add_post("/api/memory/test", routes_documents.handle_memory_test)

    # Document management
    webapp.router.add_post("/api/documents/upload", routes_documents.handle_document_upload)
    webapp.router.add_post("/api/documents/add-url", routes_documents.handle_document_add_url)
    webapp.router.add_get("/api/documents/{discussion_id}", routes_documents.handle_document_list)
    webapp.router.add_delete("/api/documents/{document_id}/{discussion_id}", routes_documents.handle_document_remove)
    webapp.router.add_delete("/api/documents/{document_id}", routes_documents.handle_document_delete)

    # Image management
    webapp.router.add_post("/api/images/upload", routes_images.handle_image_upload)
    webapp.router.add_post("/api/images/add-url", routes_images.handle_image_add_url)
    webapp.router.add_get("/api/images/file/{image_id}", routes_images.handle_image_serve)
    webapp.router.add_get("/api/images/{discussion_id}", routes_images.handle_image_list)
    webapp.router.add_delete("/api/images/{image_id}/{discussion_id}", routes_images.handle_image_remove)
    webapp.router.add_delete("/api/images/{image_id}", routes_images.handle_image_delete)

    # SSE events
    webapp.router.add_get("/api/events", routes_misc.handle_events)

    # API
    webapp.router.add_post("/api/{method}", handle_api)

    # Evaluation UI routes (before catch-all). Single-user only: /eval/api/
    # has no auth and one shared EvalDatabase, and its judge/batch routes
    # make the server call a caller-chosen provider URL.
    if multi_user:
        logger.info("Evaluation UI disabled in multi-user mode")
    else:
        try:
            from ..evaluation.eval_db import EvalDatabase
            from ..evaluation.eval_routes import register_eval_routes
            eval_db = EvalDatabase()
            register_eval_routes(webapp, eval_db)
            logger.info("Evaluation UI available at /eval/")
        except Exception:
            logger.exception("Failed to load evaluation module")

    # Static / SPA
    webapp.router.add_get("/", routes_misc.serve_index)
    webapp.router.add_get("/{path:.*}", routes_misc.serve_static)

    async def _on_startup(_webapp: web.Application) -> None:
        if ctx.session_mgr:
            ctx.session_mgr.start_cleanup_loop()

    async def _on_cleanup(_webapp: web.Application) -> None:
        # Close MCP providers for the shared app (single-user mode)
        if ctx.shared_app:
            await ctx.shared_app.shutdown()
        if ctx.session_mgr:
            await ctx.session_mgr.stop()

    webapp.on_startup.append(_on_startup)
    webapp.on_cleanup.append(_on_cleanup)
    return webapp


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
    from ..config import load_env
    load_env()

    webapp = create_web_app(host, port, multi_user)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    mode = "multi-user" if multi_user else "single-user"
    print(f"Consensus web server running at http://{host}:{port} ({mode} mode)")
    try:
        await asyncio.Event().wait()
    finally:
        # Runs the on_cleanup hooks: shared app / session manager shutdown.
        await runner.cleanup()
