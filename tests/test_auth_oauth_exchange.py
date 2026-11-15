"""Characterization tests for the OAuth helpers in ``consensus.auth``.

The provider endpoints are faked at the transport (``httpx.MockTransport``);
the real ``httpx.AsyncClient`` and the real request code run.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from consensus import auth
from consensus.auth import oauth
from consensus.auth.constants import OAUTH_MAX_RETRIES, OAUTH_RETRY_BASE_DELAY
from tests.auth_helpers import (
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    apple_id_token,
    clear_all_credentials,
    install_routes,
    set_credentials,
    token_ok,
)

GH = auth.OAUTH_PROVIDERS["github"]
GOOGLE = auth.OAUTH_PROVIDERS["google"]
LINKEDIN = auth.OAUTH_PROVIDERS["linkedin"]
APPLE = auth.OAUTH_PROVIDERS["apple"]


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    clear_all_credentials(monkeypatch)


async def _exchange(provider: str) -> dict | None:
    return await auth.exchange_oauth_code(provider, "the-code", REDIRECT_URI)


# -- Provider configuration ------------------------------------------------

def test_available_providers_lists_only_configured(monkeypatch):
    assert auth.get_available_oauth_providers() == []
    set_credentials(monkeypatch, "github", client_id="gh-id")
    assert auth.get_available_oauth_providers() == [
        {"id": "github", "name": "Github", "client_id": "gh-id"}]


def test_provider_without_secret_is_not_offered(monkeypatch):
    # Listed without a secret, it would fail only after the user consented.
    set_credentials(monkeypatch, "github", client_secret="")
    assert auth.get_available_oauth_providers() == []


def test_authorize_url_unknown_or_unconfigured_provider(monkeypatch):
    assert auth.build_oauth_authorize_url("myspace", REDIRECT_URI, "s") is None
    assert auth.build_oauth_authorize_url("github", REDIRECT_URI, "s") is None


def test_authorize_url_github(monkeypatch):
    set_credentials(monkeypatch, "github")
    url = auth.build_oauth_authorize_url("github", REDIRECT_URI, "st-1")
    parts = urlsplit(url)
    assert url.startswith(GH["authorize_url"] + "?")
    assert parse_qs(parts.query) == {
        "client_id": [CLIENT_ID], "redirect_uri": [REDIRECT_URI],
        "state": ["st-1"], "scope": [GH["scopes"]],
        "response_type": ["code"],
    }


def test_authorize_url_google_requests_offline_consent(monkeypatch):
    set_credentials(monkeypatch, "google")
    query = parse_qs(urlsplit(auth.build_oauth_authorize_url(
        "google", REDIRECT_URI, "s")).query)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]


def test_authorize_url_apple_uses_form_post(monkeypatch):
    set_credentials(monkeypatch, "apple")
    query = parse_qs(urlsplit(auth.build_oauth_authorize_url(
        "apple", REDIRECT_URI, "s")).query)
    assert query["response_mode"] == ["form_post"]


# -- Token exchange --------------------------------------------------------

async def test_exchange_unknown_provider_returns_none():
    assert await _exchange("myspace") is None


async def test_exchange_without_secret_logs_and_returns_none(monkeypatch,
                                                             caplog):
    set_credentials(monkeypatch, "github", client_secret="")
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert await _exchange("github") is None
    assert "missing client credentials" in caplog.text


async def test_exchange_posts_the_code_with_credentials(monkeypatch):
    set_credentials(monkeypatch, "google")
    seen = install_routes(monkeypatch, {
        GOOGLE["token_url"]: token_ok("tok-g"),
        GOOGLE["userinfo_url"]: httpx.Response(200, json={
            "id": 42, "email": "g@example.com", "name": "G",
            "picture": "https://p/g.png", "verified_email": True}),
    })
    info = await _exchange("google")
    assert info == {"email": "g@example.com", "name": "G",
                    "avatar_url": "https://p/g.png", "oauth_id": "42",
                    "email_verified": True}
    token_req, userinfo_req = seen
    assert token_req.method == "POST"
    assert parse_qs(token_req.content.decode()) == {
        "client_id": [CLIENT_ID], "client_secret": [CLIENT_SECRET],
        "code": ["the-code"], "redirect_uri": [REDIRECT_URI],
        "grant_type": ["authorization_code"],
    }
    assert token_req.headers["accept"] == "application/json"
    assert userinfo_req.headers["authorization"] == "Bearer tok-g"


async def test_google_email_verified_field_wins(monkeypatch):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: token_ok(),
        GOOGLE["userinfo_url"]: httpx.Response(200, json={
            "id": "1", "email": "g@example.com",
            "email_verified": "false", "verified_email": True}),
    })
    assert (await _exchange("google"))["email_verified"] is False


async def test_exchange_linkedin(monkeypatch):
    set_credentials(monkeypatch, "linkedin")
    install_routes(monkeypatch, {
        LINKEDIN["token_url"]: token_ok(),
        LINKEDIN["userinfo_url"]: httpx.Response(200, json={
            "sub": "li-7", "email": "l@example.com", "name": "L",
            "picture": "https://p/l.png", "email_verified": True}),
    })
    assert await _exchange("linkedin") == {
        "email": "l@example.com", "name": "L", "avatar_url": "https://p/l.png",
        "oauth_id": "li-7", "email_verified": True}


async def test_exchange_token_endpoint_error_returns_none(monkeypatch, caplog):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "used"})})
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert await _exchange("google") is None
    assert "token exchange failed" in caplog.text
    assert "invalid_grant" in caplog.text  # the provider's reason


async def test_exchange_token_reply_not_json_returns_none(monkeypatch):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: httpx.Response(200, text="<html>")})
    assert await _exchange("google") is None


async def test_exchange_without_access_token_returns_none(monkeypatch, caplog):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: httpx.Response(200, json={
            "error": "bad_verification_code",
            "error_description": "The code is incorrect or expired."})})
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert await _exchange("google") is None
    assert "no access_token" in caplog.text
    assert "bad_verification_code" in caplog.text
    assert "incorrect or expired" in caplog.text


@pytest.mark.parametrize("provider,profile", [
    ("github", {"id": None, "email": "n@example.com"}),
    ("google", {"id": None, "email": "n@example.com"}),
    ("linkedin", {"sub": None, "email": "n@example.com"}),
])
async def test_null_provider_id_is_no_id(monkeypatch, provider, profile):
    # str(None) == "None" would give every such reply one shared identity.
    set_credentials(monkeypatch, provider)
    cfg = auth.OAUTH_PROVIDERS[provider]
    routes = {cfg["token_url"]: token_ok(),
              cfg["userinfo_url"]: httpx.Response(200, json=profile)}
    if provider == "github":
        routes[GH["userinfo_email_url"]] = httpx.Response(200, json=[])
    install_routes(monkeypatch, routes)
    assert (await _exchange(provider))["oauth_id"] == ""


async def test_exchange_userinfo_error_returns_none(monkeypatch, caplog):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: token_ok(),
        GOOGLE["userinfo_url"]: httpx.Response(401)})
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert await _exchange("google") is None
    assert "userinfo failed" in caplog.text


# -- Retry with backoff ----------------------------------------------------

@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record backoff delays instead of sleeping."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


async def test_transient_token_failure_is_retried_with_backoff(
        monkeypatch, sleeps, caplog):
    set_credentials(monkeypatch, "google")
    seen = install_routes(monkeypatch, {
        GOOGLE["token_url"]: [httpx.Response(503), _connect_error,
                              lambda r: token_ok()],
        GOOGLE["userinfo_url"]: httpx.Response(200, json={
            "id": "1", "email": "g@example.com"}),
    })
    with caplog.at_level(logging.WARNING, logger="consensus.auth"):
        assert (await _exchange("google"))["oauth_id"] == "1"
    assert [r.url.path for r in seen].count("/token") == 3
    assert sleeps == [OAUTH_RETRY_BASE_DELAY, OAUTH_RETRY_BASE_DELAY * 2]
    assert "HTTP 503" in caplog.text and "ConnectError" in caplog.text


async def test_transient_userinfo_failure_is_retried(monkeypatch, sleeps):
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: token_ok(),
        GOOGLE["userinfo_url"]: [
            httpx.Response(429),
            lambda r: httpx.Response(200, json={"id": "2", "email": "e@x.c"}),
        ],
    })
    assert (await _exchange("google"))["oauth_id"] == "2"
    assert sleeps == [OAUTH_RETRY_BASE_DELAY]


async def test_github_email_lookup_is_retried(monkeypatch, sleeps):
    emails = [httpx.Response(502), lambda r: httpx.Response(200, json=[
        {"email": "p@example.com", "primary": True, "verified": True}])]
    _github(monkeypatch, {"id": 5, "email": "p@example.com"}, emails)
    assert (await _exchange("github"))["email_verified"] is True


@pytest.mark.parametrize("failure", [
    lambda r: httpx.Response(503), _connect_error,
])
async def test_retries_give_up_after_max_attempts(monkeypatch, sleeps,
                                                  failure):
    set_credentials(monkeypatch, "google")
    seen = install_routes(monkeypatch, {GOOGLE["token_url"]: failure})
    assert await _exchange("google") is None
    assert len(seen) == OAUTH_MAX_RETRIES
    assert len(sleeps) == OAUTH_MAX_RETRIES - 1


async def test_permanent_failure_is_not_retried(monkeypatch, sleeps):
    set_credentials(monkeypatch, "google")
    seen = install_routes(monkeypatch, {
        GOOGLE["token_url"]: httpx.Response(400, json={"error": "bad"})})
    assert await _exchange("google") is None
    assert len(seen) == 1 and sleeps == []


# -- GitHub email reconciliation -------------------------------------------

def _github(monkeypatch, profile: dict, emails) -> list[httpx.Request]:
    set_credentials(monkeypatch, "github")
    emails_reply = (httpx.Response(200, json=emails)
                    if isinstance(emails, list)
                    and all(isinstance(e, dict) for e in emails)
                    else emails)
    return install_routes(monkeypatch, {
        GH["token_url"]: token_ok("tok-gh"),
        GH["userinfo_url"]: httpx.Response(200, json=profile),
        GH["userinfo_email_url"]: emails_reply,
    })


async def test_github_primary_verified_email_wins(monkeypatch):
    seen = _github(monkeypatch,
                   {"id": 5, "login": "octo", "email": "old@example.com",
                    "avatar_url": "https://a/o.png"},
                   [{"email": "other@example.com", "verified": True},
                    {"email": "main@example.com", "primary": True,
                     "verified": True}])
    assert await _exchange("github") == {
        "email": "main@example.com", "name": "octo",
        "avatar_url": "https://a/o.png", "oauth_id": "5",
        "email_verified": True, "email_lookup_failed": False}
    assert seen[-1].headers["authorization"] == "Bearer tok-gh"


async def test_github_profile_email_reconciled_with_endpoint(monkeypatch):
    _github(monkeypatch, {"id": 5, "name": "Octo", "email": "p@example.com"},
            [{"email": "p@example.com", "primary": True, "verified": False},
             {"email": "x@example.com", "verified": True}])
    info = await _exchange("github")
    assert (info["email"], info["email_verified"]) == ("p@example.com", False)
    assert info["name"] == "Octo"


async def test_github_profile_email_verified_when_not_primary(monkeypatch):
    _github(monkeypatch, {"id": 5, "email": "p@example.com"},
            [{"email": "q@example.com", "primary": True, "verified": False},
             {"email": "p@example.com", "verified": True}])
    info = await _exchange("github")
    assert (info["email"], info["email_verified"]) == ("p@example.com", True)


async def test_github_no_profile_email_takes_first_listed(monkeypatch):
    _github(monkeypatch, {"id": 5, "email": None},
            [{"email": "first@example.com", "verified": False},
             {"email": "second@example.com", "verified": True}])
    info = await _exchange("github")
    assert (info["email"], info["email_verified"]) == (
        "first@example.com", False)


@pytest.mark.parametrize("reply,logged", [
    (httpx.Response(403), "HTTP 403"),
    (httpx.Response(200, text="not json"), "JSONDecodeError"),
    (httpx.Response(200, json={"message": "not a list"}), "AttributeError"),
])
async def test_github_emails_endpoint_failure_means_unverified(
        monkeypatch, caplog, reply, logged):
    _github(monkeypatch, {"id": 5, "email": "p@example.com"}, reply)
    with caplog.at_level(logging.WARNING, logger="consensus.auth"):
        info = await _exchange("github")
    assert (info["email"], info["email_verified"]) == ("p@example.com", False)
    assert info["email_lookup_failed"] is True
    assert "GitHub email lookup failed" in caplog.text
    assert logged in caplog.text


async def test_github_lookup_failure_logs_the_exception_text(monkeypatch,
                                                             caplog, sleeps):
    _github(monkeypatch, {"id": 5, "email": "p@example.com"}, _connect_error)
    with caplog.at_level(logging.WARNING, logger="consensus.auth"):
        await _exchange("github")
    assert "connection refused" in caplog.text


# -- Apple ID token --------------------------------------------------------

async def test_exchange_apple_reads_the_id_token(monkeypatch):
    set_credentials(monkeypatch, "apple")
    seen = install_routes(monkeypatch, {APPLE["token_url"]: token_ok(
        id_token=apple_id_token({"sub": "ap-1", "email": "a@icloud.com",
                                 "email_verified": "true"}))})
    assert await _exchange("apple") == {
        "email": "a@icloud.com", "name": "a", "avatar_url": "",
        "oauth_id": "ap-1", "email_verified": True}
    assert len(seen) == 1  # no userinfo endpoint


def test_apple_id_token_name_claim():
    token = apple_id_token({"sub": "s", "email": "e@x.com", "name": "Eve"})
    assert oauth._parse_apple_id_token({"id_token": token})["name"] == "Eve"


@pytest.mark.parametrize("token_resp", [
    {}, {"id_token": ""}, {"id_token": "only-one-part"},
])
def test_apple_missing_id_token_logs_and_returns_none(token_resp, caplog):
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert oauth._parse_apple_id_token(token_resp) is None
    assert "no usable id_token" in caplog.text


def test_apple_undecodable_id_token_logs_and_returns_none(caplog):
    with caplog.at_level(logging.ERROR, logger="consensus.auth"):
        assert oauth._parse_apple_id_token({"id_token": "a.!!!.c"}) is None
    assert "Apple ID token parse error" in caplog.text


async def test_extract_user_info_unknown_provider_returns_none():
    async with httpx.AsyncClient() as client:
        assert await oauth._extract_user_info("myspace", {}, client, {}) is None
