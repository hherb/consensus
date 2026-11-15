"""Shared fixtures for the web-server route tests (``tests/test_server_*``).

The server is driven through ``create_web_app`` and aiohttp's in-process
``TestClient``, i.e. through the real middleware stack and route table.
``HOME`` points at a temporary directory, so every database the server opens
(the shared app's, the per-session ones, ``auth.db``, ``evaluation.db``)
lands there instead of in the user's data directory.
"""

from __future__ import annotations

import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

from consensus import server
from tests.auth_helpers import fast_hashing  # noqa: F401  (re-exported fixture)

# The host/port the app is told it is bound to; the CORS middleware always
# allows the matching local origin.
TEST_HOST = "127.0.0.1"
TEST_PORT = 8080
LOCAL_ORIGIN = f"http://{TEST_HOST}:{TEST_PORT}"

TEST_EMAIL = "alice@example.com"
TEST_PASSWORD = "correct horse battery"

# Environment that changes server behaviour; cleared so the developer's
# shell cannot leak into a test.
_SERVER_ENV = (
    "CONSENSUS_ALLOWED_ORIGINS", "CONSENSUS_BASE_URL", "XDG_DATA_HOME",
)


def module_of(anchor: object):
    """Return the module that defines *anchor* (a function or class)."""
    return sys.modules[anchor.__module__]


@pytest.fixture
def server_home(tmp_path, monkeypatch):
    """Redirect the platform data directory into *tmp_path*."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in _SERVER_ENV:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


async def make_client(multi_user: bool) -> TestClient:
    """Start a ``TestClient`` over a freshly built app (caller closes it)."""
    webapp = server.create_web_app(TEST_HOST, TEST_PORT, multi_user=multi_user)
    client = TestClient(TestServer(webapp))
    await client.start_server()
    return client


@pytest.fixture
async def single_client(server_home):
    """A client of a single-user server (one shared ``ConsensusApp``)."""
    client = await make_client(multi_user=False)
    yield client
    await client.close()


@pytest.fixture
async def multi_client(server_home, fast_hashing):  # noqa: F811
    """A client of a ``--multi-user`` server (auth enforced on ``/api/``)."""
    client = await make_client(multi_user=True)
    yield client
    await client.close()


async def register(client: TestClient, email: str = TEST_EMAIL,
                   password: str = TEST_PASSWORD) -> dict:
    """Register a user; the auth cookie lands in the client's jar."""
    resp = await client.post("/auth/register", json={
        "email": email, "password": password, "display_name": "Alice",
    })
    assert resp.status == 200, await resp.text()
    return (await resp.json())["user"]


def auth_cookie(client: TestClient) -> str:
    """Return the auth token the client's cookie jar holds ("" if none)."""
    for cookie in client.session.cookie_jar:
        if cookie.key == server.AUTH_COOKIE:
            return cookie.value
    return ""


@pytest.fixture
async def signed_in(multi_client):
    """A multi-user client that has registered (and so holds an auth cookie)."""
    await register(multi_client)
    return multi_client


async def api(client: TestClient, method: str, payload: dict | None = None,
              **kwargs):
    """POST to the JSON dispatch route ``/api/<method>``."""
    return await client.post(f"/api/{method}", json=payload or {}, **kwargs)
