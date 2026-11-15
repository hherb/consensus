"""Route tests for ``/auth/*``: password sign-in, profile and the OAuth flow.

``AuthManager`` itself is covered by ``tests/test_auth_*``; these tests pin
what the HTTP layer adds — status codes, the auth cookie, the per-email
brute-force limit, and the OAuth redirect/state/callback handling.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from consensus import server
from consensus.auth import AUTH_TOKEN_TTL, AuthManager
from tests.auth_helpers import clear_all_credentials, set_credentials
from tests.server_helpers import (  # noqa: F401  (fixtures)
    TEST_EMAIL, TEST_PASSWORD, api, auth_cookie, fast_hashing, module_of,
    multi_client, register, server_home, signed_in, single_client,
)


def login_limit_module():
    """The module whose ``LOGIN_RATE_LIMIT_*`` the login handler reads."""
    from consensus.server import routes_auth
    return routes_auth


async def login(client, email=TEST_EMAIL, password=TEST_PASSWORD):
    return await client.post("/auth/login",
                             json={"email": email, "password": password})


class TestAuthDisabled:
    """Single-user mode: the auth routes answer 'not enabled'."""

    @pytest.mark.parametrize("path", ["/auth/register", "/auth/login",
                                      "/auth/me"])
    async def test_post_routes_are_404(self, single_client, path):
        resp = await single_client.post(path, json={})
        assert resp.status == 404

    async def test_status_reports_auth_not_required(self, single_client):
        body = await (await single_client.get("/auth/status")).json()
        assert body == {"auth_required": False, "authenticated": False,
                        "user": None}

    async def test_logout_still_clears_the_cookie(self, single_client):
        resp = await single_client.post("/auth/logout", json={})
        assert (await resp.json()) == {"ok": True}
        assert resp.cookies[server.AUTH_COOKIE].value == ""

    async def test_oauth_routes_are_404(self, single_client):
        for path in ("/auth/oauth/authorize/github",
                     "/auth/oauth/callback/github?code=c&state=s"):
            resp = await single_client.get(path, allow_redirects=False)
            assert resp.status == 404, path

    async def test_me_is_401(self, single_client):
        assert (await single_client.get("/auth/me")).status == 401


class TestRegister:
    async def test_sets_an_httponly_auth_cookie(self, multi_client):
        resp = await multi_client.post("/auth/register", json={
            "email": TEST_EMAIL, "password": TEST_PASSWORD,
            "display_name": "Alice"})
        assert resp.status == 200
        body = await resp.json()
        assert body["user"]["email"] == TEST_EMAIL
        assert body["user"]["display_name"] == "Alice"
        assert "token" not in body
        cookie = resp.cookies[server.AUTH_COOKIE]
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"
        assert int(cookie["max-age"]) == AUTH_TOKEN_TTL
        assert not cookie["secure"]

    async def test_invalid_input_is_a_400(self, multi_client):
        resp = await multi_client.post("/auth/register", json={
            "email": "not-an-email", "password": TEST_PASSWORD})
        assert resp.status == 400
        assert "error" in await resp.json()

    async def test_duplicate_email_is_a_400(self, multi_client):
        await register(multi_client)
        resp = await multi_client.post("/auth/register", json={
            "email": TEST_EMAIL, "password": TEST_PASSWORD})
        assert resp.status == 400

    async def test_bad_json_is_a_400(self, multi_client):
        resp = await multi_client.post(
            "/auth/register", data=b"{",
            headers={"Content-Type": "application/json"})
        assert resp.status == 400
        assert (await resp.json()) == {"error": "Invalid JSON"}


class TestLogin:
    async def test_success_sets_the_cookie(self, multi_client):
        await register(multi_client)
        multi_client.session.cookie_jar.clear()
        resp = await login(multi_client)
        assert resp.status == 200
        assert (await resp.json())["user"]["email"] == TEST_EMAIL
        assert resp.cookies[server.AUTH_COOKIE].value
        assert (await api(multi_client, "get_state")).status == 200

    async def test_email_is_normalised(self, multi_client):
        await register(multi_client)
        resp = await login(multi_client, email="  ALICE@Example.com ")
        assert resp.status == 200

    async def test_wrong_password_is_a_401(self, multi_client):
        await register(multi_client)
        resp = await login(multi_client, password="wrong")
        assert resp.status == 401
        assert "error" in await resp.json()

    async def test_bad_json_is_a_400(self, multi_client):
        resp = await multi_client.post(
            "/auth/login", data=b"[",
            headers={"Content-Type": "application/json"})
        assert resp.status == 400

    async def test_brute_force_limit_per_email(self, multi_client):
        await register(multi_client)
        limit = login_limit_module().LOGIN_RATE_LIMIT_MAX
        for _ in range(limit):
            assert (await login(multi_client, password="wrong")).status == 401
        # Locked out even with the right password ...
        resp = await login(multi_client)
        assert resp.status == 429
        assert "Too many login attempts" in (await resp.json())["error"]
        # ... but only for that email.
        resp = await login(multi_client, email="bob@example.com",
                           password="wrong")
        assert resp.status == 401

    async def test_successful_logins_are_not_counted(self, multi_client):
        await register(multi_client)
        limit = login_limit_module().LOGIN_RATE_LIMIT_MAX
        for _ in range(limit + 1):
            assert (await login(multi_client)).status == 200

    async def test_lockout_expires_with_the_window(self, multi_client,
                                                   monkeypatch):
        mod = login_limit_module()
        await register(multi_client)
        clock = [1_000_000.0]
        monkeypatch.setattr(mod.time, "time", lambda: clock[0])
        for _ in range(mod.LOGIN_RATE_LIMIT_MAX):
            await login(multi_client, password="wrong")
        assert (await login(multi_client)).status == 429
        clock[0] += mod.LOGIN_RATE_LIMIT_WINDOW + 1
        assert (await login(multi_client)).status == 200

    async def test_blank_email_is_not_tracked(self, multi_client):
        limit = login_limit_module().LOGIN_RATE_LIMIT_MAX
        for _ in range(limit + 1):
            resp = await login(multi_client, email="")
            assert resp.status == 401


class TestLogoutAndStatus:
    async def test_status_when_signed_out(self, multi_client, monkeypatch):
        clear_all_credentials(monkeypatch)
        set_credentials(monkeypatch, "github")
        body = await (await multi_client.get("/auth/status")).json()
        assert body["auth_required"] is True
        assert body["authenticated"] is False
        assert body["user"] is None
        assert [p["id"] if isinstance(p, dict) else p
                for p in body["oauth_providers"]] == ["github"]

    async def test_status_when_signed_in(self, signed_in):
        body = await (await signed_in.get("/auth/status")).json()
        assert body["authenticated"] is True
        assert body["user"]["email"] == TEST_EMAIL

    async def test_logout_revokes_the_token(self, signed_in):
        token = auth_cookie(signed_in)
        resp = await signed_in.post("/auth/logout", json={})
        assert (await resp.json()) == {"ok": True}
        body = await (await signed_in.get(
            "/auth/status",
            headers={"Authorization": f"Bearer {token}"})).json()
        assert body["authenticated"] is False


class TestProfile:
    async def test_update_requires_auth(self, multi_client):
        resp = await multi_client.post("/auth/me", json={"display_name": "x"})
        assert resp.status == 401


class TestOAuth:
    async def test_providers_lists_configured_ones(self, single_client,
                                                   monkeypatch):
        clear_all_credentials(monkeypatch)
        set_credentials(monkeypatch, "google")
        body = await (await single_client.get("/auth/oauth/providers")).json()
        assert [p["id"] if isinstance(p, dict) else p
                for p in body["providers"]] == ["google"]

    async def test_authorize_unconfigured_provider_is_a_400(
            self, multi_client, monkeypatch):
        clear_all_credentials(monkeypatch)
        resp = await multi_client.get("/auth/oauth/authorize/github",
                                      allow_redirects=False)
        assert resp.status == 400
        assert "not configured" in (await resp.json())["error"]

    async def _authorize(self, client, monkeypatch, provider="github"):
        clear_all_credentials(monkeypatch)
        set_credentials(monkeypatch, provider)
        resp = await client.get(f"/auth/oauth/authorize/{provider}",
                                allow_redirects=False)
        assert resp.status == 302
        query = parse_qs(urlparse(resp.headers["Location"]).query)
        return query["state"][0], query["redirect_uri"][0]

    async def test_authorize_uses_the_configured_base_url(
            self, multi_client, monkeypatch):
        monkeypatch.setenv("CONSENSUS_BASE_URL", "https://consensus.test/")
        _state, redirect_uri = await self._authorize(multi_client, monkeypatch)
        assert redirect_uri == \
            "https://consensus.test/auth/oauth/callback/github"

    async def test_authorize_falls_back_to_the_request_host(
            self, multi_client, monkeypatch, caplog):
        _state, redirect_uri = await self._authorize(multi_client, monkeypatch)
        host = f"{multi_client.host}:{multi_client.port}"
        assert redirect_uri == f"http://{host}/auth/oauth/callback/github"
        assert "CONSENSUS_BASE_URL not set" in caplog.text

    async def test_callback_without_code_shows_the_escaped_error(
            self, multi_client):
        resp = await multi_client.get(
            "/auth/oauth/callback/github?error=<script>x</script>")
        assert resp.status == 400
        text = await resp.text()
        assert "&lt;script&gt;" in text and "<script>" not in text

    async def test_callback_with_unknown_state_is_a_400(self, multi_client):
        resp = await multi_client.get(
            "/auth/oauth/callback/github?code=c&state=forged")
        assert resp.status == 400
        assert "Invalid OAuth State" in await resp.text()

    async def test_callback_state_is_bound_to_its_provider(
            self, multi_client, monkeypatch):
        state, _ = await self._authorize(multi_client, monkeypatch)
        resp = await multi_client.get(
            f"/auth/oauth/callback/google?code=c&state={state}")
        assert resp.status == 400
        assert "Invalid OAuth State" in await resp.text()

    async def test_callback_state_is_single_use(self, multi_client,
                                                monkeypatch):
        state, _ = await self._authorize(multi_client, monkeypatch)
        self._fake_exchange(monkeypatch, [])
        url = f"/auth/oauth/callback/github?code=c&state={state}"
        first = await multi_client.get(url, allow_redirects=False)
        assert first.status == 302
        second = await multi_client.get(url, allow_redirects=False)
        assert second.status == 400

    @staticmethod
    def _fake_exchange(monkeypatch, calls, error: str = ""):
        async def exchange(self, provider, code, redirect_uri):
            calls.append((provider, code, redirect_uri))
            if error:
                raise ValueError(error)
            return self.register(email="oauth@example.com",
                                 password=TEST_PASSWORD)
        monkeypatch.setattr(AuthManager, "oauth_callback", exchange)

    async def test_callback_success_sets_the_cookie_and_redirects(
            self, multi_client, monkeypatch):
        state, redirect_uri = await self._authorize(multi_client, monkeypatch)
        calls: list = []
        self._fake_exchange(monkeypatch, calls)
        resp = await multi_client.get(
            f"/auth/oauth/callback/github?code=the-code&state={state}",
            allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/"
        cookie = resp.cookies[server.AUTH_COOKIE]
        assert cookie.value and cookie["httponly"]
        assert calls == [("github", "the-code", redirect_uri)]
        assert (await api(multi_client, "get_state")).status == 200

    async def test_callback_form_post_is_accepted(self, multi_client,
                                                  monkeypatch):
        state, _ = await self._authorize(multi_client, monkeypatch, "apple")
        calls: list = []
        self._fake_exchange(monkeypatch, calls)
        resp = await multi_client.post(
            "/auth/oauth/callback/apple",
            data={"code": "the-code", "state": state}, allow_redirects=False)
        assert resp.status == 302
        assert calls[0][:2] == ("apple", "the-code")

    async def test_callback_exchange_failure_is_escaped(self, multi_client,
                                                        monkeypatch):
        state, _ = await self._authorize(multi_client, monkeypatch)
        self._fake_exchange(monkeypatch, [], error="bad <b>code</b>")
        resp = await multi_client.get(
            f"/auth/oauth/callback/github?code=c&state={state}")
        assert resp.status == 400
        text = await resp.text()
        assert "bad &lt;b&gt;code&lt;/b&gt;" in text


class TestMe:
    """``/auth/me`` reads and edits the signed-in user's profile."""

    async def test_get_returns_the_user(self, signed_in):
        resp = await signed_in.get("/auth/me")
        assert resp.status == 200
        assert (await resp.json())["user"]["email"] == TEST_EMAIL

    async def test_get_requires_auth(self, multi_client):
        resp = await multi_client.get("/auth/me")
        assert resp.status == 401

    async def test_update_changes_only_profile_fields(self, signed_in):
        resp = await signed_in.post("/auth/me", json={
            "display_name": "Alice B", "avatar_url": "https://a/x.png",
            "email": "mallory@example.com", "is_active": False})
        assert resp.status == 200
        user = (await resp.json())["user"]
        assert user["display_name"] == "Alice B"
        assert user["avatar_url"] == "https://a/x.png"
        assert user["email"] == TEST_EMAIL

    async def test_update_with_nothing_to_change(self, signed_in):
        resp = await signed_in.post("/auth/me", json={"email": "x@y.z"})
        assert resp.status == 200
        assert (await resp.json())["user"]["email"] == TEST_EMAIL

    async def test_update_bad_json_is_a_400(self, signed_in):
        resp = await signed_in.post(
            "/auth/me", data=b"{", headers={"Content-Type": "application/json"})
        assert resp.status == 400


class TestNonObjectBodies:
    """A JSON body that is not an object is a 400, not a crash."""

    @pytest.mark.parametrize("path", ["/auth/register", "/auth/login"])
    async def test_auth_routes(self, multi_client, path):
        resp = await multi_client.post(path, json=["a"])
        assert resp.status == 400
        assert (await resp.json()) == {"error": "Invalid JSON"}

    async def test_profile_update(self, signed_in):
        resp = await signed_in.post("/auth/me", json="x")
        assert resp.status == 400

    async def test_login_non_string_email(self, multi_client):
        resp = await multi_client.post("/auth/login",
                                       json={"email": 5, "password": "x"})
        assert resp.status == 400
