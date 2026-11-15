"""Shared fakes for the ``consensus.auth`` tests.

Everything is patched through the module that *defines* the patched name
(``sys.modules[anchor.__module__]``), so the tests keep constraining the
code now that ``auth.py`` is split into a package.
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Callable

import httpx
import pytest

from consensus import auth
from tests.image_helpers import install_transport

#: PBKDF2 rounds used by the tests; production's 600k cost 0.13 s per hash.
FAST_HASH_ITERATIONS = 1_000

#: OAuth client credentials the fake environment provides.
CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"

#: Redirect URI used throughout the OAuth tests.
REDIRECT_URI = "https://consensus.test/auth/oauth/callback/x"


def module_of(anchor: object):
    """Return the module that defines *anchor* (a function or class)."""
    return sys.modules[anchor.__module__]


@pytest.fixture
def fast_hashing(monkeypatch):
    """Lower the PBKDF2 iteration count where ``hash_password`` reads it."""
    monkeypatch.setattr(module_of(auth.hash_password),
                        "PASSWORD_HASH_ITERATIONS", FAST_HASH_ITERATIONS)


@pytest.fixture
def auth_db(tmp_path, fast_hashing):
    """A real ``AuthDatabase`` on a temporary file."""
    db = auth.AuthDatabase(str(tmp_path / "auth.db"))
    yield db
    db.conn.close()


@pytest.fixture
def manager(auth_db):
    """An ``AuthManager`` over the temporary database."""
    return auth.AuthManager(auth_db)


def set_credentials(monkeypatch, provider: str,
                    client_id: str = CLIENT_ID,
                    client_secret: str = CLIENT_SECRET) -> None:
    """Configure *provider*'s client credentials in the environment."""
    cfg = auth.SIGN_IN_PROVIDERS[provider]
    monkeypatch.setenv(cfg["env_client_id"], client_id)
    monkeypatch.setenv(cfg["env_client_secret"], client_secret)


def clear_all_credentials(monkeypatch) -> None:
    """Remove every provider's client credentials from the environment."""
    for cfg in auth.SIGN_IN_PROVIDERS.values():
        monkeypatch.delenv(cfg["env_client_id"], raising=False)
        monkeypatch.delenv(cfg["env_client_secret"], raising=False)


def apple_id_token(payload: dict) -> str:
    """Build an unsigned JWT ``header.payload.signature`` carrying *payload*."""
    def segment(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{segment({'alg': 'none'})}.{segment(payload)}.sig"


Route = httpx.Response | Callable[[httpx.Request], httpx.Response] | list


def install_routes(monkeypatch, routes: dict[str, Route]
                   ) -> list[httpx.Request]:
    """Answer each request by its URL (without query) from *routes*.

    A route value is a response, a handler, or a list of either consumed
    one per request (the last one repeats), so retries can be scripted.
    An unrouted URL fails the test with a 599 the code under test sees.

    Returns:
        Every request seen, in order.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url.copy_with(query=None))
        route = routes.get(key)
        if route is None:
            return httpx.Response(599, text=f"unrouted {key}")
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        return route(request) if callable(route) else route

    return install_transport(monkeypatch, handler)


def token_ok(access_token: str = "access-1", **extra) -> httpx.Response:
    """A successful token-endpoint reply."""
    return httpx.Response(200, json={"access_token": access_token, **extra})
