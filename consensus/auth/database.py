"""SQLite persistence for users, auth tokens, OAuth identities and states."""

import os
import secrets
import sqlite3
import threading
import time
from typing import Optional

from .constants import AUTH_TOKEN_BYTES, AUTH_TOKEN_TTL, OAUTH_STATE_TTL
from .models import User
from .passwords import _hash_token, hash_password

class AuthDatabase:
    """SQLite persistence for users and auth tokens.

    Backed by a dedicated ``auth.db``; used only in multi-user mode.
    """

    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    email           TEXT NOT NULL UNIQUE,
                    display_name    TEXT NOT NULL DEFAULT '',
                    password_hash   TEXT NOT NULL DEFAULT '',
                    oauth_provider  TEXT NOT NULL DEFAULT '',
                    oauth_id        TEXT NOT NULL DEFAULT '',
                    avatar_url      TEXT NOT NULL DEFAULT '',
                    is_active       INTEGER NOT NULL DEFAULT 1,
                    created_at      REAL NOT NULL,
                    last_login      REAL NOT NULL DEFAULT 0
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth
                    ON users(oauth_provider, oauth_id)
                    WHERE oauth_provider != '';

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    token_hash  TEXT NOT NULL UNIQUE,
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash
                    ON auth_tokens(token_hash);

                CREATE TABLE IF NOT EXISTS user_oauth_identities (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    provider    TEXT NOT NULL,
                    oauth_id    TEXT NOT NULL,
                    avatar_url  TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(provider, oauth_id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_oauth_identities_user
                    ON user_oauth_identities(user_id);

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state       TEXT PRIMARY KEY,
                    provider    TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                );
            """)

    def _query_one(self, sql: str,
                   params: tuple = ()) -> Optional[sqlite3.Row]:
        """Fetch one row under the lock.

        Login and registration run in worker threads (#141); without the
        lock a read could land inside another thread's multi-statement
        write (``link_oauth``) on the shared connection.
        """
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def _execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    # -- User CRUD ---------------------------------------------------------

    def create_user(self, email: str, display_name: str = "",
                    password: str = "", oauth_provider: str = "",
                    oauth_id: str = "", avatar_url: str = "") -> User:
        """Create a new user.

        Raises ValueError if the email, or the (oauth_provider, oauth_id)
        pair, is already registered.
        """
        now = time.time()
        pw_hash = hash_password(password) if password else ""
        try:
            cur = self._execute_write(
                """INSERT INTO users
                   (email, display_name, password_hash, oauth_provider,
                    oauth_id, avatar_url, is_active, created_at, last_login)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (email, display_name or email.split("@")[0], pw_hash,
                 oauth_provider, oauth_id, avatar_url, now, now),
            )
        except sqlite3.IntegrityError as e:
            if "users.email" in str(e):
                raise ValueError(f"User with email '{email}' already exists")
            raise ValueError(
                f"This {oauth_provider} account is already registered")
        return User(
            id=cur.lastrowid or 0, email=email,
            display_name=display_name or email.split("@")[0],
            password_hash=pw_hash, oauth_provider=oauth_provider,
            oauth_id=oauth_id, avatar_url=avatar_url,
            is_active=True, created_at=now, last_login=now,
        )

    def get_user_by_email(self, email: str) -> Optional[User]:
        row = self._query_one(
            "SELECT * FROM users WHERE email = ?", (email,),
        )
        return User.from_db_row(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        row = self._query_one(
            "SELECT * FROM users WHERE id = ?", (user_id,),
        )
        return User.from_db_row(row) if row else None

    def get_user_by_oauth(self, provider: str, oauth_id: str) -> Optional[User]:
        # Check the multi-identity table first
        row = self._query_one(
            """SELECT u.* FROM user_oauth_identities oi
               JOIN users u ON u.id = oi.user_id
               WHERE oi.provider = ? AND oi.oauth_id = ?""",
            (provider, oauth_id),
        )
        if row:
            return User.from_db_row(row)
        # Fall back to legacy single-identity columns
        row = self._query_one(
            "SELECT * FROM users WHERE oauth_provider = ? AND oauth_id = ?",
            (provider, oauth_id),
        )
        return User.from_db_row(row) if row else None

    def update_last_login(self, user_id: int) -> None:
        self._execute_write(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (time.time(), user_id),
        )

    def update_user(self, user_id: int, **kwargs: object) -> None:
        """Update user fields. Only known safe fields are accepted.

        ``email`` is deliberately not editable: nothing verifies a new
        address, and OAuth sign-in links a verified provider email to the
        account holding it, so an edit could claim someone else's address.
        """
        allowed = {"display_name", "avatar_url"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        self._execute_write(
            f"UPDATE users SET {set_clause} WHERE id = ?", tuple(values),
        )

    def change_password(self, user_id: int, new_password: str,
                        expected: Optional[str] = None) -> bool:
        """Store a fresh hash of *new_password*; True if a row changed.

        With *expected*, write only while the stored hash still equals it,
        so a concurrent change (an OAuth lock-out, say) is not undone.
        """
        pw_hash = hash_password(new_password)
        sql = "UPDATE users SET password_hash = ? WHERE id = ?"
        params: tuple = (pw_hash, user_id)
        if expected is not None:
            sql += " AND password_hash = ?"
            params += (expected,)
        return self._execute_write(sql, params).rowcount > 0

    def link_oauth(self, user_id: int, provider: str, oauth_id: str,
                   avatar_url: str = "",
                   lock_out_password: bool = False) -> None:
        """Link an OAuth identity to an existing user.

        Supports multiple OAuth identities per user via the
        user_oauth_identities table. Also updates legacy columns
        on the users table for backwards compatibility.

        With *lock_out_password*, also clears the password and revokes every
        session, in the same transaction: a link that left a squatter's
        sessions alive would be worse than no link.
        """
        now = time.time()
        sql = "UPDATE users SET oauth_provider = ?, oauth_id = ?"
        params: list = [provider, oauth_id]
        if avatar_url:
            sql += ", avatar_url = ?"
            params.append(avatar_url)
        sql += " WHERE id = ?"
        params.append(user_id)
        with self._lock, self.conn:
            # A user who signed up via OAuth has that identity only in the
            # legacy columns; keep it before they are overwritten below.
            self.conn.execute(
                """INSERT OR IGNORE INTO user_oauth_identities
                   (user_id, provider, oauth_id, avatar_url, created_at)
                   SELECT id, oauth_provider, oauth_id, avatar_url, created_at
                   FROM users WHERE id = ? AND oauth_provider != ''""",
                (user_id,),
            )
            # OR IGNORE: the identity may already be linked to this user.
            self.conn.execute(
                """INSERT OR IGNORE INTO user_oauth_identities
                   (user_id, provider, oauth_id, avatar_url, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, provider, oauth_id, avatar_url, now),
            )
            # Legacy columns track the most recent OAuth identity.
            self.conn.execute(sql, tuple(params))
            if lock_out_password:
                self.conn.execute(
                    "UPDATE users SET password_hash = '' WHERE id = ?",
                    (user_id,))
                self.conn.execute(
                    "DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))

    # -- Auth tokens -------------------------------------------------------

    def create_token(self, user_id: int, ttl: int = AUTH_TOKEN_TTL,
                     password_hash: Optional[str] = None) -> str:
        """Create a new auth token. Returns the raw token string.

        Expired tokens are pruned first; nothing else removes them.

        With *password_hash*, the token is issued only while the user is
        active and still stores that hash, in one statement: a password
        login verifies off the event loop (#141), and an OAuth lock-out or
        a disable landing meanwhile must not be followed by a fresh
        session. Raises ValueError if the condition fails.
        """
        self.cleanup_expired_tokens()
        raw_token = secrets.token_urlsafe(AUTH_TOKEN_BYTES)
        now = time.time()
        params: tuple = (user_id, _hash_token(raw_token), now, now + ttl)
        if password_hash is None:
            self._execute_write(
                """INSERT INTO auth_tokens
                   (user_id, token_hash, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""", params)
            return raw_token
        cur = self._execute_write(
            """INSERT INTO auth_tokens
               (user_id, token_hash, created_at, expires_at)
               SELECT ?, ?, ?, ? WHERE EXISTS (
                   SELECT 1 FROM users WHERE id = ? AND password_hash = ?
                   AND password_hash != '' AND is_active = 1)""",
            params + (user_id, password_hash))
        if cur.rowcount == 0:
            raise ValueError("Invalid email or password")
        return raw_token

    def validate_token(self, raw_token: str) -> Optional[User]:
        """Validate a token and return the associated user, or None."""
        th = _hash_token(raw_token)
        row = self._query_one(
            """SELECT u.* FROM auth_tokens t
               JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.expires_at > ?
               AND u.is_active = 1""",
            (th, time.time()),
        )
        return User.from_db_row(row) if row else None

    def revoke_token(self, raw_token: str) -> None:
        self._execute_write(
            "DELETE FROM auth_tokens WHERE token_hash = ?",
            (_hash_token(raw_token),),
        )

    def revoke_all_tokens(self, user_id: int) -> None:
        self._execute_write(
            "DELETE FROM auth_tokens WHERE user_id = ?", (user_id,),
        )

    def cleanup_expired_tokens(self) -> int:
        cur = self._execute_write(
            "DELETE FROM auth_tokens WHERE expires_at < ?", (time.time(),),
        )
        return cur.rowcount

    # -- OAuth state -------------------------------------------------------

    def store_oauth_state(self, state: str, provider: str,
                          redirect_uri: str = "") -> None:
        """Record a pending OAuth flow's state, pruning abandoned ones."""
        self.cleanup_expired_states()
        self._execute_write(
            "INSERT INTO oauth_states (state, provider, redirect_uri, created_at) VALUES (?, ?, ?, ?)",
            (state, provider, redirect_uri, time.time()),
        )

    def consume_oauth_state(self, state: str) -> Optional[dict]:
        """Atomically validate and consume an OAuth state token.

        The delete-and-return runs as a single statement under the lock, so
        two concurrent callbacks with the same state cannot both succeed
        (which would weaken CSRF/replay protection).  Returns provider info,
        or None if the state was unknown, already consumed, or expired.
        """
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM oauth_states WHERE state = ? "
                "RETURNING provider, redirect_uri, created_at",
                (state,),
            )
            row = cur.fetchone()
            self.conn.commit()
        if not row:
            return None
        if time.time() - row["created_at"] > OAUTH_STATE_TTL:
            return None
        return {"provider": row["provider"], "redirect_uri": row["redirect_uri"]}

    def cleanup_expired_states(self) -> None:
        """Remove OAuth state tokens older than OAUTH_STATE_TTL."""
        self._execute_write(
            "DELETE FROM oauth_states WHERE created_at < ?",
            (time.time() - OAUTH_STATE_TTL,),
        )


