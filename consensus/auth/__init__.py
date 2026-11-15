"""Authentication and user registration for Consensus.

Used only in multi-user web mode (``--web --multi-user``); desktop and
single-user web run without authentication.  Two ways to sign in:
- Email + password registration, no external deps.
- OAuth: GitHub, Google, LinkedIn, Apple sign-in via the standard
  Authorization Code flow.

OAuth requires ``httpx`` (already a project dependency) for token exchange.
No additional libraries are needed.

The package splits the former ``auth.py`` (issue #61): ``constants``,
``models``, ``passwords``, ``database`` (``AuthDatabase``), ``oauth``
(provider helpers and code exchange) and ``manager`` (``AuthManager``).
This facade re-exports the public API; private helpers are imported from
their submodule.
"""

from .constants import (
    AUTH_TOKEN_BYTES,
    AUTH_TOKEN_TTL,
    MIN_PASSWORD_LENGTH,
    OAUTH_STATE_TTL,
    PASSWORD_HASH_ITERATIONS,
    PASSWORD_SALT_BYTES,
    SIGN_IN_HTTP_TIMEOUT,
    SIGN_IN_MAX_RETRIES,
    SIGN_IN_PROVIDERS,
    SIGN_IN_RETRY_BASE_DELAY,
)
from .database import AuthDatabase
from .manager import AuthManager
from .models import AuthToken, User
from .oauth import (
    build_oauth_authorize_url,
    exchange_oauth_code,
    get_available_oauth_providers,
)
from .passwords import hash_password, verify_password

__all__ = [
    "AUTH_TOKEN_BYTES",
    "AUTH_TOKEN_TTL",
    "MIN_PASSWORD_LENGTH",
    "OAUTH_STATE_TTL",
    "PASSWORD_HASH_ITERATIONS",
    "PASSWORD_SALT_BYTES",
    "SIGN_IN_HTTP_TIMEOUT",
    "SIGN_IN_MAX_RETRIES",
    "SIGN_IN_PROVIDERS",
    "SIGN_IN_RETRY_BASE_DELAY",
    "AuthDatabase",
    "AuthManager",
    "AuthToken",
    "User",
    "build_oauth_authorize_url",
    "exchange_oauth_code",
    "get_available_oauth_providers",
    "hash_password",
    "verify_password",
]
