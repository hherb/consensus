"""Settings and wire names for the web server (``consensus.server``)."""

from typing import Awaitable, Callable

from aiohttp import web

# Type alias for aiohttp request handlers
RequestHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]

# Default server settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

# Rate limiting defaults
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 120  # requests per window

# Login brute-force protection
LOGIN_RATE_LIMIT_MAX = 5  # max login attempts per email per window
LOGIN_RATE_LIMIT_WINDOW = 300  # 5-minute window

# Session cookie name
SESSION_COOKIE = "consensus_sid"

# Auth cookie/header name
AUTH_COOKIE = "consensus_auth"
AUTH_HEADER = "Authorization"

# A POST to /api/ or /auth/ must be JSON or carry this header (the frontend
# sets it on multipart uploads). A cross-site HTML form can do neither, and a
# cross-origin fetch doing either needs a CORS preflight this server never
# grants — that is the CSRF protection.
CSRF_HEADER = "X-Consensus-Request"

# Served images get no script and an opaque origin (sandbox); inline
# styles stay so an SVG still renders as drawn.
IMAGE_SERVE_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"

# An idle SSE stream gets a keepalive comment this often (seconds).
SSE_KEEPALIVE_SECONDS = 30

# The 503 body when --multi-user has no room for another session.
CAPACITY_ERROR = "Server is at capacity. Please try again later."

# The 400 body when a route that cannot create a --multi-user session (any
# /api/ route but POST /api/<method>) finds none for the request's cookie.
NO_SESSION_ERROR = "No active session. Reload the page."
