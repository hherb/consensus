"""Characterization tests for ``consensus.auth.AuthManager``.

Registration, login, logout and the OAuth find-or-create logic run against a
real ``AuthDatabase``. OAuth callbacks go through the real
``exchange_oauth_code`` with the provider faked at the transport, so the
manager's wiring to the exchange is constrained too.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from consensus import auth
from tests.auth_helpers import (  # noqa: F401
    REDIRECT_URI,
    auth_db,
    clear_all_credentials,
    fast_hashing,
    install_routes,
    manager,
    set_credentials,
    token_ok,
)

GOOGLE = auth.OAUTH_PROVIDERS["google"]


@pytest.fixture
def google(monkeypatch):
    """Fake Google; returns a setter for the userinfo the next login sees."""
    clear_all_credentials(monkeypatch)
    set_credentials(monkeypatch, "google")
    reply: dict = {}

    def userinfo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=reply)

    install_routes(monkeypatch, {GOOGLE["token_url"]: lambda r: token_ok(),
                                 GOOGLE["userinfo_url"]: userinfo})

    def set_reply(**fields) -> None:
        reply.clear()
        reply.update(fields)
    return set_reply


async def _google_login(manager) -> tuple[auth.User, str]:
    return await manager.oauth_callback("google", "code", REDIRECT_URI)


# -- Registration ----------------------------------------------------------

def test_register_normalises_email_and_returns_a_valid_token(manager):
    user, token = manager.register("  Ann@Example.COM ", "long-enough",
                                   display_name="Ann")
    assert user.email == "ann@example.com"
    assert user.display_name == "Ann"
    assert manager.get_current_user(token).id == user.id


@pytest.mark.parametrize("email", ["", "   ", "no-at-sign"])
def test_register_rejects_invalid_email(manager, email):
    with pytest.raises(ValueError, match="Valid email"):
        manager.register(email, "long-enough")


def test_register_rejects_short_password(manager):
    with pytest.raises(ValueError, match="at least 8"):
        manager.register("a@example.com", "short77")
    manager.register("a@example.com", "exactly8")


def test_register_duplicate_email_raises(manager):
    manager.register("a@example.com", "long-enough")
    with pytest.raises(ValueError, match="already exists"):
        manager.register("A@example.com", "long-enough")


# -- Login / logout --------------------------------------------------------

def test_login_with_correct_password(manager):
    registered, _ = manager.register("a@example.com", "long-enough")
    user, token = manager.login(" A@EXAMPLE.com", "long-enough")
    assert user.id == registered.id
    assert manager.get_current_user(token).id == registered.id
    assert manager.db.get_user_by_id(user.id).last_login >= user.last_login


@pytest.mark.parametrize("email,password", [
    ("a@example.com", "wrong-password"),
    ("nobody@example.com", "long-enough"),
])
def test_login_failures_share_one_message(manager, email, password):
    manager.register("a@example.com", "long-enough")
    with pytest.raises(ValueError, match="^Invalid email or password$"):
        manager.login(email, password)


def test_login_refused_for_oauth_only_account(manager):
    manager.db.create_user(email="o@example.com", oauth_provider="github",
                           oauth_id="1")
    with pytest.raises(ValueError, match="Invalid email or password"):
        manager.login("o@example.com", "")


def test_login_refused_for_disabled_account(manager):
    user, _ = manager.register("a@example.com", "long-enough")
    manager.db.conn.execute("UPDATE users SET is_active = 0 WHERE id = ?",
                            (user.id,))
    with pytest.raises(ValueError, match="disabled"):
        manager.login("a@example.com", "long-enough")
    # Without the password, the account's status is not revealed.
    with pytest.raises(ValueError, match="^Invalid email or password$"):
        manager.login("a@example.com", "wrong-password")


def test_logout_revokes_only_that_session(manager):
    manager.register("a@example.com", "long-enough")
    _, first = manager.login("a@example.com", "long-enough")
    _, second = manager.login("a@example.com", "long-enough")
    manager.logout(first)
    assert manager.get_current_user(first) is None
    assert manager.get_current_user(second) is not None


@pytest.mark.parametrize("token", ["", None])
def test_get_current_user_without_token(manager, token):
    assert manager.get_current_user(token) is None


# -- OAuth callback --------------------------------------------------------

async def test_oauth_first_login_creates_user(manager, google):
    google(id="g-1", email="New@Example.com", name="Newt",
           picture="https://p/n.png", verified_email=True)
    user, token = await _google_login(manager)
    assert user.email == "new@example.com"
    assert user.display_name == "Newt"
    assert user.avatar_url == "https://p/n.png"
    assert manager.get_current_user(token).id == user.id
    assert manager.db.get_user_by_oauth("google", "g-1").id == user.id


async def test_oauth_repeat_login_finds_the_same_user(manager, google):
    google(id="g-1", email="n@example.com", verified_email=True)
    first, _ = await _google_login(manager)
    # The provider-side email changed; the identity still finds the account.
    google(id="g-1", email="renamed@example.com", verified_email=False)
    again, token = await _google_login(manager)
    assert again.id == first.id
    assert manager.get_current_user(token).id == first.id


async def test_oauth_verified_email_links_existing_account(manager, google):
    existing = manager.db.create_user(email="a@example.com")
    google(id="g-2", email="A@example.com", picture="https://p/a.png",
           verified_email=True)
    user, token = await _google_login(manager)
    assert user.id == existing.id
    assert manager.db.get_user_by_oauth("google", "g-2").id == existing.id
    assert manager.db.get_user_by_id(existing.id).avatar_url == (
        "https://p/a.png")
    assert manager.get_current_user(token).id == existing.id


async def test_oauth_link_drops_password_and_sessions_set_by_others(
        manager, google):
    # Registration never verifies the email, so whoever set this password
    # may not own the address; the provider just proved who does.
    squatter, squatter_token = manager.register("v@example.com", "attacker1")
    google(id="g-5", email="v@example.com", verified_email=True)
    user, token = await _google_login(manager)
    assert user.id == squatter.id
    assert user.password_hash == ""
    assert manager.get_current_user(squatter_token) is None
    assert manager.get_current_user(token).id == user.id
    with pytest.raises(ValueError, match="Invalid email or password"):
        manager.login("v@example.com", "attacker1")


async def test_oauth_link_without_password_keeps_sessions(manager, google):
    owner = manager.db.create_user(email="o@example.com",
                                   oauth_provider="github", oauth_id="gh-1")
    session = manager.db.create_token(owner.id)
    google(id="g-6", email="o@example.com", verified_email=True)
    await _google_login(manager)
    assert manager.get_current_user(session).id == owner.id


async def test_oauth_link_keeps_the_identity_the_user_signed_up_with(
        manager, google):
    # Sign-up via GitHub stores the identity only in the legacy columns,
    # which a later link overwrites.
    owner = manager.db.create_user(email="o@example.com",
                                   oauth_provider="github", oauth_id="gh-1")
    google(id="g-7", email="o@example.com", verified_email=True)
    await _google_login(manager)
    assert manager.db.get_user_by_oauth("github", "gh-1").id == owner.id
    assert manager.db.get_user_by_oauth("google", "g-7").id == owner.id


async def test_oauth_unverified_email_never_links(manager, google):
    manager.db.create_user(email="a@example.com", password="long-enough")
    google(id="g-3", email="a@example.com", verified_email=False)
    with pytest.raises(ValueError, match="did not confirm"):
        await _google_login(manager)
    assert manager.db.get_user_by_oauth("google", "g-3") is None


@pytest.mark.parametrize("reply", [{}, {"id": "g-4", "email": ""}])
async def test_oauth_without_email_is_refused(manager, google, reply):
    google(**reply)
    with pytest.raises(ValueError, match="Could not retrieve user info"):
        await _google_login(manager)


@pytest.mark.parametrize("id_field", [{}, {"id": None}, {"id": ""}])
async def test_oauth_without_provider_id_is_refused(manager, google, caplog,
                                                    id_field):
    # An empty id would match any other account stored with an empty id,
    # signing this person in as someone else.
    manager.db.create_user(email="first@example.com",
                           oauth_provider="google", oauth_id="")
    google(email="second@example.com", verified_email=True, **id_field)
    with caplog.at_level(logging.WARNING, logger="consensus.auth"):
        with pytest.raises(ValueError, match="Could not retrieve user info"):
            await _google_login(manager)
    assert "no email or user id" in caplog.text


async def test_oauth_provider_failure_is_refused(manager, monkeypatch):
    clear_all_credentials(monkeypatch)
    set_credentials(monkeypatch, "google")
    install_routes(monkeypatch, {
        GOOGLE["token_url"]: httpx.Response(400, json={"error": "x"})})
    with pytest.raises(ValueError, match="from google"):
        await _google_login(manager)


GH = auth.OAUTH_PROVIDERS["github"]


@pytest.mark.parametrize("profile_email", ["a@example.com", None])
async def test_github_email_lookup_outage_asks_to_retry(
        manager, monkeypatch, profile_email):
    # An outage is not evidence the email is unverified; saying so would
    # send the user off to another sign-in method for no reason.
    manager.db.create_user(email="a@example.com", password="long-enough")
    clear_all_credentials(monkeypatch)
    set_credentials(monkeypatch, "github")
    install_routes(monkeypatch, {
        GH["token_url"]: token_ok(),
        GH["userinfo_url"]: httpx.Response(200, json={
            "id": 9, "email": profile_email}),
        GH["userinfo_email_url"]: httpx.Response(403),
    })
    with pytest.raises(ValueError, match="try again") as exc:
        await manager.oauth_callback("github", "code", REDIRECT_URI)
    assert "did not confirm" not in str(exc.value)
    assert manager.db.get_user_by_oauth("github", "9") is None
