"""Web server mode (aiohttp): ``python -m consensus --web``.

Split from the former ``server.py`` (issue #61). ``launch_web`` builds the
app with ``create_web_app`` (``app_factory``) and serves it. Per-server
state lives in one ``ServerContext`` on the app (``context``); the
middleware stack is in ``middleware``; routes are grouped by area in
``routes_api`` (the ``/api/<method>`` dispatch), ``routes_auth``,
``routes_documents`` (memory config + documents), ``routes_images`` and
``routes_misc`` (health, SSE, static). Patch names in the submodule that
*uses* them — this facade only re-exports.
"""

from .app_factory import create_web_app, launch_web
from .constants import (
    AUTH_COOKIE,
    AUTH_HEADER,
    CSRF_HEADER,
    DEFAULT_HOST,
    DEFAULT_PORT,
    IMAGE_SERVE_CSP,
    SESSION_COOKIE,
)
from .context import CTX_KEY, ServerContext

__all__ = [
    "AUTH_COOKIE",
    "AUTH_HEADER",
    "CSRF_HEADER",
    "CTX_KEY",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "IMAGE_SERVE_CSP",
    "SESSION_COOKIE",
    "ServerContext",
    "create_web_app",
    "launch_web",
]
