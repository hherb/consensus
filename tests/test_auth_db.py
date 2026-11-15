"""Characterization tests for ``consensus.auth`` passwords and ``AuthDatabase``.

Real SQLite on a temp file; only the clock and the PBKDF2 cost are faked.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from consensus import auth
from tests.auth_helpers import auth_db, fast_hashing, module_of  # noqa: F401


def _advance_clock(monkeypatch, anchor, seconds: float) -> None:
    """Make ``time.time()`` in *anchor*'s module run *seconds* ahead."""
    mod = module_of(anchor)
    real = mod.time.time

    class _Clock:
        @staticmethod
        def time() -> float:
            return real() + seconds

    monkeypatch.setattr(mod, "time", _Clock)


# -- Passwords -------------------------------------------------------------

def test_hash_password_uses_production_iterations():
    stored = auth.hash_password("correct horse")
    salt_hex, hash_hex = stored.split("$")
    assert len(bytes.fromhex(salt_hex)) == auth.PASSWORD_SALT_BYTES
    expected = hashlib.pbkdf2_hmac(
        "sha256", b"correct horse", bytes.fromhex(salt_hex),
        auth.PASSWORD_HASH_ITERATIONS).hex()
    assert hash_hex == expected
    assert auth.PASSWORD_HASH_ITERATIONS >= 600_000


def test_hash_password_salts_every_hash(fast_hashing):
    assert auth.hash_password("pw") != auth.hash_password("pw")


def test_verify_password_round_trip(fast_hashing):
    stored = auth.hash_password("s3cret-pw")
    assert auth.verify_password("s3cret-pw", stored)
    assert not auth.verify_password("s3cret-pX", stored)


@pytest.mark.parametrize("stored", ["", "no-dollar", "zz$abcd", None])
def test_verify_password_rejects_malformed_hash(stored, fast_hashing):
    assert auth.verify_password("pw", stored) is False


# -- User ------------------------------------------------------------------

def test_user_to_dict_omits_secrets():
    user = auth.User(id=3, email="a@b.c", password_hash="salt$hash",
                     oauth_id="gh-1", oauth_provider="github")
    data = user.to_dict()
    assert "password_hash" not in data
    assert "oauth_id" not in data
    assert data["id"] == 3 and data["oauth_provider"] == "github"


# -- Users -----------------------------------------------------------------

def test_create_user_persists_and_defaults_display_name(auth_db):
    user = auth_db.create_user(email="ann@example.com", password="pw123456")
    assert user.id > 0
    assert user.display_name == "ann"
    stored = auth_db.get_user_by_email("ann@example.com")
    assert stored.id == user.id
    assert stored.display_name == "ann"
    assert auth.verify_password("pw123456", stored.password_hash)
    assert auth_db.get_user_by_id(user.id).email == "ann@example.com"


def test_create_user_without_password_stores_empty_hash(auth_db):
    user = auth_db.create_user(email="o@example.com", oauth_provider="github",
                               oauth_id="7", avatar_url="https://a/x.png")
    stored = auth_db.get_user_by_id(user.id)
    assert stored.password_hash == ""
    assert stored.avatar_url == "https://a/x.png"


def test_create_user_duplicate_email_raises_value_error(auth_db):
    auth_db.create_user(email="dup@example.com")
    with pytest.raises(ValueError, match="already exists"):
        auth_db.create_user(email="dup@example.com")


def test_unknown_user_lookups_return_none(auth_db):
    assert auth_db.get_user_by_email("nobody@example.com") is None
    assert auth_db.get_user_by_id(999) is None
    assert auth_db.get_user_by_oauth("github", "nope") is None


def test_update_last_login(auth_db, monkeypatch):
    user = auth_db.create_user(email="l@example.com")
    _advance_clock(monkeypatch, auth.AuthDatabase, 100)
    auth_db.update_last_login(user.id)
    assert auth_db.get_user_by_id(user.id).last_login > user.last_login + 50


def test_update_user_ignores_unknown_fields(auth_db):
    user = auth_db.create_user(email="u@example.com")
    auth_db.update_user(user.id, display_name="New", is_active=0,
                        password_hash="x")
    stored = auth_db.get_user_by_id(user.id)
    assert stored.display_name == "New"
    assert stored.is_active is True
    assert stored.password_hash == ""


def test_update_user_cannot_change_email(auth_db):
    # An unverified email edit plus OAuth auto-linking let an attacker claim
    # an address before its owner first signed in with a provider.
    user = auth_db.create_user(email="mine@example.com")
    auth_db.update_user(user.id, email="victim@example.com")
    assert auth_db.get_user_by_id(user.id).email == "mine@example.com"
    assert auth_db.get_user_by_email("victim@example.com") is None


def test_update_user_with_no_allowed_fields_is_a_no_op(auth_db):
    user = auth_db.create_user(email="n@example.com")
    auth_db.update_user(user.id, is_active=0)
    auth_db.update_user(user.id)
    assert auth_db.get_user_by_id(user.id).is_active is True


def test_change_password(auth_db):
    user = auth_db.create_user(email="c@example.com", password="old-pass")
    auth_db.change_password(user.id, "new-pass")
    stored = auth_db.get_user_by_id(user.id).password_hash
    assert auth.verify_password("new-pass", stored)
    assert not auth.verify_password("old-pass", stored)


# -- OAuth identities ------------------------------------------------------

def test_link_oauth_supports_several_identities(auth_db):
    user = auth_db.create_user(email="m@example.com")
    auth_db.link_oauth(user.id, "github", "gh-1", avatar_url="https://a/1")
    auth_db.link_oauth(user.id, "google", "go-1")
    assert auth_db.get_user_by_oauth("github", "gh-1").id == user.id
    assert auth_db.get_user_by_oauth("google", "go-1").id == user.id
    stored = auth_db.get_user_by_id(user.id)
    # Legacy columns track the latest identity; an empty avatar keeps the old.
    assert (stored.oauth_provider, stored.oauth_id) == ("google", "go-1")
    assert stored.avatar_url == "https://a/1"


def test_link_oauth_twice_is_idempotent(auth_db):
    user = auth_db.create_user(email="i@example.com")
    auth_db.link_oauth(user.id, "github", "gh-2")
    auth_db.link_oauth(user.id, "github", "gh-2")
    count = auth_db.conn.execute(
        "SELECT COUNT(*) FROM user_oauth_identities").fetchone()[0]
    assert count == 1


def test_get_user_by_oauth_falls_back_to_legacy_columns(auth_db):
    user = auth_db.create_user(email="leg@example.com",
                               oauth_provider="github", oauth_id="gh-9")
    assert auth_db.get_user_by_oauth("github", "gh-9").id == user.id
    assert auth_db.get_user_by_oauth("google", "gh-9") is None


def test_legacy_oauth_pair_is_unique(auth_db):
    auth_db.create_user(email="a@example.com", oauth_provider="github",
                        oauth_id="same")
    with pytest.raises(ValueError):
        auth_db.create_user(email="b@example.com", oauth_provider="github",
                            oauth_id="same")


# -- Tokens ----------------------------------------------------------------

def test_token_is_stored_only_as_its_sha256(auth_db):
    user = auth_db.create_user(email="t@example.com")
    raw = auth_db.create_token(user.id)
    rows = auth_db.conn.execute("SELECT token_hash FROM auth_tokens").fetchall()
    assert [r[0] for r in rows] == [hashlib.sha256(raw.encode()).hexdigest()]
    assert raw not in rows[0][0]
    assert auth_db.validate_token(raw).id == user.id


def test_tokens_are_unique(auth_db):
    user = auth_db.create_user(email="t2@example.com")
    assert auth_db.create_token(user.id) != auth_db.create_token(user.id)


def test_validate_unknown_token_returns_none(auth_db):
    assert auth_db.validate_token("not-a-token") is None


def test_token_expires_after_ttl(auth_db, monkeypatch):
    user = auth_db.create_user(email="e@example.com")
    raw = auth_db.create_token(user.id, ttl=60)
    _advance_clock(monkeypatch, auth.AuthDatabase, 61)
    assert auth_db.validate_token(raw) is None


def test_default_token_ttl_is_thirty_days(auth_db, monkeypatch):
    user = auth_db.create_user(email="d@example.com")
    raw = auth_db.create_token(user.id)
    _advance_clock(monkeypatch, auth.AuthDatabase, auth.AUTH_TOKEN_TTL - 60)
    assert auth_db.validate_token(raw) is not None
    assert auth.AUTH_TOKEN_TTL == 86400 * 30


def test_token_of_inactive_user_is_rejected(auth_db):
    user = auth_db.create_user(email="x@example.com")
    raw = auth_db.create_token(user.id)
    auth_db.conn.execute("UPDATE users SET is_active = 0 WHERE id = ?",
                         (user.id,))
    assert auth_db.validate_token(raw) is None


def test_revoke_token_revokes_only_that_token(auth_db):
    user = auth_db.create_user(email="r@example.com")
    first, second = auth_db.create_token(user.id), auth_db.create_token(user.id)
    auth_db.revoke_token(first)
    assert auth_db.validate_token(first) is None
    assert auth_db.validate_token(second).id == user.id


def test_revoke_all_tokens_spares_other_users(auth_db):
    ann = auth_db.create_user(email="ann@example.com")
    bob = auth_db.create_user(email="bob@example.com")
    ann_tokens = [auth_db.create_token(ann.id) for _ in range(2)]
    bob_token = auth_db.create_token(bob.id)
    auth_db.revoke_all_tokens(ann.id)
    assert all(auth_db.validate_token(t) is None for t in ann_tokens)
    assert auth_db.validate_token(bob_token).id == bob.id


def test_cleanup_expired_tokens(auth_db):
    user = auth_db.create_user(email="ce@example.com")
    live = auth_db.create_token(user.id)
    auth_db.create_token(user.id, ttl=-1)
    assert auth_db.cleanup_expired_tokens() == 1
    assert auth_db.validate_token(live) is not None


def test_deleting_a_user_cascades_to_tokens(auth_db):
    user = auth_db.create_user(email="del@example.com")
    auth_db.create_token(user.id)
    auth_db.link_oauth(user.id, "github", "gh-del")
    auth_db._execute_write("DELETE FROM users WHERE id = ?", (user.id,))
    for table in ("auth_tokens", "user_oauth_identities"):
        count = auth_db.conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0


# -- OAuth state -----------------------------------------------------------

def test_unknown_state_is_rejected(auth_db):
    assert auth_db.consume_oauth_state("never-stored") is None


def test_expired_state_is_rejected_and_consumed(auth_db, monkeypatch):
    auth_db.store_oauth_state("s-old", "github", "https://x/cb")
    _advance_clock(monkeypatch, auth.AuthDatabase, auth.OAUTH_STATE_TTL + 1)
    assert auth_db.consume_oauth_state("s-old") is None
    count = auth_db.conn.execute(
        "SELECT COUNT(*) FROM oauth_states").fetchone()[0]
    assert count == 0


def test_duplicate_state_is_refused(auth_db):
    auth_db.store_oauth_state("s-dup", "github")
    with pytest.raises(sqlite3.IntegrityError):
        auth_db.store_oauth_state("s-dup", "google")


def test_cleanup_expired_states_keeps_fresh_ones(auth_db, monkeypatch):
    auth_db.store_oauth_state("s-stale", "github")
    _advance_clock(monkeypatch, auth.AuthDatabase, auth.OAUTH_STATE_TTL + 1)
    auth_db.store_oauth_state("s-fresh", "google")
    auth_db.cleanup_expired_states()
    remaining = [r[0] for r in auth_db.conn.execute(
        "SELECT state FROM oauth_states")]
    assert remaining == ["s-fresh"]


# -- Construction ----------------------------------------------------------

def test_database_creates_missing_parent_dir_and_reopens(tmp_path,
                                                         fast_hashing):
    path = tmp_path / "nested" / "dir" / "auth.db"
    db = auth.AuthDatabase(str(path))
    db.create_user(email="keep@example.com")
    db.conn.close()
    reopened = auth.AuthDatabase(str(path))
    assert reopened.get_user_by_email("keep@example.com") is not None
    assert reopened.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    reopened.conn.close()
