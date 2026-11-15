"""Route tests for the web server's middleware stack.

Security headers, the CORS origin check, per-IP rate limiting, the
Content-Type CSRF guard and the ``--multi-user`` authentication gate, all
driven through the real app built by ``create_web_app``.
"""

from __future__ import annotations

import re
from pathlib import Path

import aiohttp
import pytest

from consensus import server
from tests.server_helpers import (  # noqa: F401  (fixtures)
    LOCAL_ORIGIN, api, auth_cookie, fast_hashing,
    make_client, module_of, multi_client, register, server_home, signed_in,
    single_client,
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def rate_limit_module():
    """The module whose ``RATE_LIMIT_*`` / ``time`` the limiter reads."""
    return module_of(server.create_web_app)


class TestSecurityHeaders:
    @pytest.mark.parametrize("path", ["/health", "/", "/api/no_such_method",
                                      "/auth/status"])
    async def test_every_response_carries_them(self, single_client, path):
        resp = await (single_client.post(path, json={}) if path.startswith("/api")
                      else single_client.get(path))
        for name, value in SECURITY_HEADERS.items():
            assert resp.headers[name] == value


class TestCors:
    async def test_no_origin_is_allowed(self, single_client):
        resp = await api(single_client, "list_discussion_methods")
        assert resp.status == 200

    async def test_local_origin_is_allowed(self, single_client):
        resp = await api(single_client, "list_discussion_methods",
                         headers={"Origin": LOCAL_ORIGIN})
        assert resp.status == 200

    async def test_unknown_origin_is_forbidden(self, single_client):
        resp = await api(single_client, "list_discussion_methods",
                         headers={"Origin": "https://evil.example"})
        assert resp.status == 403
        assert (await resp.json()) == {"error": "Forbidden origin"}

    async def test_non_api_paths_are_not_checked(self, single_client):
        resp = await single_client.get(
            "/health", headers={"Origin": "https://evil.example"})
        assert resp.status == 200

    async def test_configured_origin_is_allowed(self, server_home,
                                                monkeypatch):
        monkeypatch.setenv("CONSENSUS_ALLOWED_ORIGINS",
                           " https://a.example , ,https://b.example")
        client = await make_client(multi_user=False)
        try:
            for origin in ("https://a.example", "https://b.example"):
                resp = await api(client, "list_discussion_methods",
                                 headers={"Origin": origin})
                assert resp.status == 200, origin
            resp = await api(client, "list_discussion_methods",
                             headers={"Origin": "https://c.example"})
            assert resp.status == 403
        finally:
            await client.close()

    async def test_multi_user_without_origins_warns(self, server_home,
                                                    fast_hashing, caplog):
        client = await make_client(multi_user=True)
        await client.close()
        assert "CONSENSUS_ALLOWED_ORIGINS" in caplog.text


class TestRateLimit:
    async def test_limit_per_window(self, single_client, monkeypatch):
        monkeypatch.setattr(rate_limit_module(), "RATE_LIMIT_MAX", 3)
        statuses = [
            (await api(single_client, "list_discussion_methods")).status
            for _ in range(4)
        ]
        assert statuses == [200, 200, 200, 429]
        body = await (await api(single_client, "get_state")).json()
        assert "Rate limit exceeded" in body["error"]

    async def test_non_api_paths_are_not_limited(self, single_client,
                                                 monkeypatch):
        monkeypatch.setattr(rate_limit_module(), "RATE_LIMIT_MAX", 1)
        for _ in range(3):
            assert (await single_client.get("/health")).status == 200

    async def test_window_expiry_frees_the_bucket(self, single_client,
                                                  monkeypatch):
        mod = rate_limit_module()
        monkeypatch.setattr(mod, "RATE_LIMIT_MAX", 1)
        clock = [1_000_000.0]
        monkeypatch.setattr(mod.time, "time", lambda: clock[0])
        assert (await api(single_client, "get_state")).status == 200
        assert (await api(single_client, "get_state")).status == 429
        clock[0] += mod.RATE_LIMIT_WINDOW + 1
        assert (await api(single_client, "get_state")).status == 200


class TestCsrf:
    @pytest.mark.parametrize("path", ["/api/get_state", "/auth/login",
                                      "/auth/register", "/api/memory/test"])
    async def test_non_json_post_is_rejected(self, single_client, path):
        resp = await single_client.post(
            path, data="a=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert resp.status == 415
        assert "application/json" in (await resp.json())["error"]

    async def test_post_without_content_type_is_rejected(self, single_client):
        resp = await single_client.post("/api/get_state", data=b"")
        assert resp.status == 415

    @pytest.mark.parametrize("path", ["/api/documents/upload",
                                      "/api/images/upload"])
    async def test_multipart_needs_the_csrf_header(self, single_client, path):
        form = aiohttp.FormData()
        form.add_field("file", b"x", filename="x.txt")
        resp = await single_client.post(path, data=form)
        assert resp.status == 415
        assert server.CSRF_HEADER in (await resp.json())["error"]

    async def test_csrf_header_admits_a_non_json_post(self, single_client):
        resp = await single_client.post(
            "/api/memory/test", data=b"",
            headers={server.CSRF_HEADER: "1", "Content-Type": "text/plain"})
        assert resp.status != 415

    async def test_oauth_callback_form_post_is_exempt(self, single_client):
        # Reaches the handler (auth disabled → 404), not the CSRF 415.
        resp = await single_client.post(
            "/auth/oauth/callback/apple", data={"code": "c", "state": "s"})
        assert resp.status == 404

    async def test_json_charset_is_accepted(self, single_client):
        resp = await single_client.post(
            "/api/get_state", data=b"{}",
            headers={"Content-Type": "application/json; charset=utf-8"})
        assert resp.status == 200

    async def test_non_post_methods_are_not_checked(self, single_client):
        resp = await single_client.put("/api/memory/config", data=b"x=1",
                                       headers={"Content-Type": "text/plain"})
        assert resp.status != 415


class TestAuthGate:
    async def test_single_user_needs_no_auth(self, single_client):
        assert (await api(single_client, "get_state")).status == 200

    async def test_multi_user_api_requires_auth(self, multi_client):
        resp = await api(multi_client, "get_state")
        assert resp.status == 401
        assert (await resp.json()) == {"error": "Authentication required"}

    @pytest.mark.parametrize("path", ["/health", "/auth/status", "/",
                                      "/auth/oauth/providers"])
    async def test_non_api_paths_are_open(self, multi_client, path):
        assert (await multi_client.get(path)).status == 200

    async def test_cookie_authenticates(self, signed_in):
        assert (await api(signed_in, "get_state")).status == 200

    async def test_bearer_header_authenticates(self, signed_in):
        token = auth_cookie(signed_in)
        signed_in.session.cookie_jar.clear()
        resp = await api(signed_in, "get_state",
                         headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200

    async def test_bad_token_is_rejected(self, multi_client):
        resp = await api(multi_client, "get_state",
                         headers={"Authorization": "Bearer nope"})
        assert resp.status == 401

    async def test_bearer_header_wins_over_cookie(self, signed_in):
        resp = await api(signed_in, "get_state",
                         headers={"Authorization": "Bearer nope"})
        assert resp.status == 401

    async def test_logout_revokes_access(self, signed_in):
        token = auth_cookie(signed_in)
        await signed_in.post("/auth/logout", json={})
        resp = await api(signed_in, "get_state",
                         headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 401



STATIC_DIR = Path(server.__file__).resolve().parent / "static"
FETCH_CALL = re.compile(r"fetch\((?P<args>[^;]*?)\);", re.S)


def test_frontend_posts_pass_the_csrf_guard():
    """Every frontend ``fetch`` POST is JSON or carries ``CSRF_HEADER``.

    There is no JS test harness; this scan is what keeps a new POST (or a
    dropped header) from being answered 415 in web mode — uploads, logout
    and the embedding test all were.
    """
    offenders = []
    for js in sorted(STATIC_DIR.rglob("*.js")):
        for match in FETCH_CALL.finditer(js.read_text()):
            args = match.group("args")
            if "'POST'" not in args:
                continue
            if ("'Content-Type': 'application/json'" in args
                    or "UPLOAD_HEADERS" in args or "headers," in args):
                continue
            offenders.append(f"{js.name}: fetch({args.strip()[:60]}...)")
    assert offenders == []


def test_frontend_upload_header_matches_the_server():
    api_js = (STATIC_DIR / "api.js").read_text()
    assert f"const UPLOAD_HEADERS = {{ '{server.CSRF_HEADER}': '1' }};" \
        in api_js
