"""Tests for OAuth account-linking safety in AuthManager.oauth_callback.

Regression coverage for the account-takeover risk where an OAuth login was
linked to an existing account purely by matching email, without checking
that the provider had verified that email.
"""

from __future__ import annotations

import pytest

import consensus.auth as auth_mod
from consensus.auth import AuthDatabase, AuthManager, _coerce_verified


@pytest.fixture
def manager(tmp_path):
    db = AuthDatabase(str(tmp_path / "auth.db"))
    return AuthManager(db)


def _patch_exchange(monkeypatch, userinfo):
    async def fake_exchange(provider, code, redirect_uri):
        return userinfo

    monkeypatch.setattr(auth_mod, "exchange_oauth_code", fake_exchange)


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    ("true", True), ("TRUE", True), ("false", False),
    ("", False), (None, False), (1, True), (0, False),
])
def test_coerce_verified(value, expected):
    assert _coerce_verified(value) is expected


@pytest.mark.asyncio
async def test_unverified_email_does_not_link_existing_account(manager, monkeypatch):
    # Pre-existing password account.
    manager.db.create_user(email="victim@example.com", password="pw")
    _patch_exchange(monkeypatch, {
        "email": "victim@example.com",
        "name": "Attacker",
        "avatar_url": "",
        "oauth_id": "attacker-123",
        "email_verified": False,
    })
    with pytest.raises(ValueError):
        await manager.oauth_callback("google", "code", "https://x/cb")
    # No OAuth identity was linked to the victim.
    assert manager.db.get_user_by_oauth("google", "attacker-123") is None


@pytest.mark.asyncio
async def test_verified_email_links_existing_account(manager, monkeypatch):
    user = manager.db.create_user(email="real@example.com", password="pw")
    _patch_exchange(monkeypatch, {
        "email": "real@example.com",
        "name": "Real",
        "avatar_url": "",
        "oauth_id": "real-456",
        "email_verified": True,
    })
    linked_user, token = await manager.oauth_callback("google", "code", "https://x/cb")
    assert linked_user.id == user.id
    assert token
    assert manager.db.get_user_by_oauth("google", "real-456").id == user.id


@pytest.mark.asyncio
async def test_new_user_created_even_if_unverified(manager, monkeypatch):
    # No existing account → creating a brand new user carries no takeover risk.
    _patch_exchange(monkeypatch, {
        "email": "new@example.com",
        "name": "New",
        "avatar_url": "",
        "oauth_id": "new-789",
        "email_verified": False,
    })
    user, token = await manager.oauth_callback("google", "code", "https://x/cb")
    assert user.email == "new@example.com"
    assert token
