"""Authentication and user registration for Consensus.

Supports two modes:
- Simple registration (default / desktop): email + password, no external deps.
- OAuth (hosted / multi-user web): GitHub, Google, LinkedIn, Apple sign-in
  via standard Authorization Code flow.

OAuth requires ``httpx`` (already a project dependency) for token exchange.
No additional libraries are needed.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_TOKEN_BYTES = 32
AUTH_TOKEN_TTL = 86400 * 30  # 30 days
PASSWORD_SALT_BYTES = 32
PASSWORD_HASH_ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256

# OAuth provider definitions (client IDs/secrets come from env vars)
OAUTH_PROVIDERS = {
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "userinfo_email_url": "https://api.github.com/user/emails",
        "scopes": "read:user user:email",
        "env_client_id": "CONSENSUS_GITHUB_CLIENT_ID",
        "env_client_secret": "CONSENSUS_GITHUB_CLIENT_SECRET",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": "openid email profile",
        "env_client_id": "CONSENSUS_GOOGLE_CLIENT_ID",
        "env_client_secret": "CONSENSUS_GOOGLE_CLIENT_SECRET",
    },
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "userinfo_url": "https://api.linkedin.com/v2/userinfo",
        "scopes": "openid profile email",
        "env_client_id": "CONSENSUS_LINKEDIN_CLIENT_ID",
        "env_client_secret": "CONSENSUS_LINKEDIN_CLIENT_SECRET",
    },
    "apple": {
        "authorize_url": "https://appleid.apple.com/auth/authorize",
        "token_url": "https://appleid.apple.com/auth/token",
        "userinfo_url": "",  # Apple returns user info in the ID token
        "scopes": "name email",
        "env_client_id": "CONSENSUS_APPLE_CLIENT_ID",
        "env_client_secret": "CONSENSUS_APPLE_CLIENT_SECRET",
        "response_mode": "form_post",
    },
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class User:
    """A registered user."""
    id: int = 0
    email: str = ""
    display_name: str = ""
    password_hash: str = ""  # empty for OAuth-only users
    oauth_provider: str = ""  # e.g. "github", "google"
    oauth_id: str = ""  # provider-specific user ID
    avatar_url: str = ""
    is_active: bool = True
    created_at: float = 0.0
    last_login: float = 0.0

    def to_dict(self) -> dict:
        """Public-safe serialization (no password hash)."""
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "oauth_provider": self.oauth_provider,
            "avatar_url": self.avatar_url,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_login": self.last_login,
        }

    @classmethod
    def from_db_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            password_hash=row["password_hash"] or "",
            oauth_provider=row["oauth_provider"] or "",
            oauth_id=row["oauth_id"] or "",
            avatar_url=row["avatar_url"] or "",
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            last_login=row["last_login"] or 0.0,
        )


@dataclass
class AuthToken:
    """A session/auth token."""
    id: int = 0
    user_id: int = 0
    token_hash: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-SHA256, no external deps)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256. Returns 'salt$hash' hex string."""
    salt = os.urandom(PASSWORD_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS,
    )
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored 'salt$hash' string."""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _hash_token(token: str) -> str:
    """Hash a bearer token for storage (SHA-256)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Auth database
# ---------------------------------------------------------------------------

class AuthDatabase:
    """SQLite persistence for users and auth tokens.

    Shares the same database file as the main app when in single-user mode,
    or uses a dedicated auth.db in multi-user mode.
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

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state       TEXT PRIMARY KEY,
                    provider    TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                );
            """)

    def _execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    # -- User CRUD ---------------------------------------------------------

    def create_user(self, email: str, display_name: str = "",
                    password: str = "", oauth_provider: str = "",
                    oauth_id: str = "", avatar_url: str = "") -> User:
        """Create a new user. Raises ValueError if email already exists."""
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
        except sqlite3.IntegrityError:
            raise ValueError(f"User with email '{email}' already exists")
        return User(
            id=cur.lastrowid or 0, email=email,
            display_name=display_name or email.split("@")[0],
            password_hash=pw_hash, oauth_provider=oauth_provider,
            oauth_id=oauth_id, avatar_url=avatar_url,
            is_active=True, created_at=now, last_login=now,
        )

    def get_user_by_email(self, email: str) -> Optional[User]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,),
        ).fetchone()
        return User.from_db_row(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        return User.from_db_row(row) if row else None

    def get_user_by_oauth(self, provider: str, oauth_id: str) -> Optional[User]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE oauth_provider = ? AND oauth_id = ?",
            (provider, oauth_id),
        ).fetchone()
        return User.from_db_row(row) if row else None

    def update_last_login(self, user_id: int) -> None:
        self._execute_write(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (time.time(), user_id),
        )

    def update_user(self, user_id: int, **kwargs: object) -> None:
        """Update user fields. Only known safe fields are accepted."""
        allowed = {"display_name", "avatar_url", "email"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        self._execute_write(
            f"UPDATE users SET {set_clause} WHERE id = ?", tuple(values),
        )

    def change_password(self, user_id: int, new_password: str) -> None:
        pw_hash = hash_password(new_password)
        self._execute_write(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (pw_hash, user_id),
        )

    def link_oauth(self, user_id: int, provider: str, oauth_id: str,
                   avatar_url: str = "") -> None:
        """Link an OAuth identity to an existing user."""
        sql = "UPDATE users SET oauth_provider = ?, oauth_id = ?"
        params: list = [provider, oauth_id]
        if avatar_url:
            sql += ", avatar_url = ?"
            params.append(avatar_url)
        sql += " WHERE id = ?"
        params.append(user_id)
        self._execute_write(sql, tuple(params))

    # -- Auth tokens -------------------------------------------------------

    def create_token(self, user_id: int,
                     ttl: int = AUTH_TOKEN_TTL) -> str:
        """Create a new auth token. Returns the raw token string."""
        raw_token = secrets.token_urlsafe(AUTH_TOKEN_BYTES)
        now = time.time()
        self._execute_write(
            """INSERT INTO auth_tokens (user_id, token_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, _hash_token(raw_token), now, now + ttl),
        )
        return raw_token

    def validate_token(self, raw_token: str) -> Optional[User]:
        """Validate a token and return the associated user, or None."""
        th = _hash_token(raw_token)
        row = self.conn.execute(
            """SELECT u.* FROM auth_tokens t
               JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.expires_at > ?
               AND u.is_active = 1""",
            (th, time.time()),
        ).fetchone()
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
        self._execute_write(
            "INSERT INTO oauth_states (state, provider, redirect_uri, created_at) VALUES (?, ?, ?, ?)",
            (state, provider, redirect_uri, time.time()),
        )

    def consume_oauth_state(self, state: str) -> Optional[dict]:
        """Validate and consume an OAuth state token. Returns provider info or None."""
        row = self.conn.execute(
            "SELECT * FROM oauth_states WHERE state = ?", (state,),
        ).fetchone()
        if not row:
            return None
        self._execute_write("DELETE FROM oauth_states WHERE state = ?", (state,))
        # Expire states older than 10 minutes
        if time.time() - row["created_at"] > 600:
            return None
        return {"provider": row["provider"], "redirect_uri": row["redirect_uri"]}

    def cleanup_expired_states(self) -> None:
        self._execute_write(
            "DELETE FROM oauth_states WHERE created_at < ?",
            (time.time() - 600,),
        )


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def get_available_oauth_providers() -> list[dict]:
    """Return list of OAuth providers that have client credentials configured."""
    available = []
    for name, cfg in OAUTH_PROVIDERS.items():
        client_id = os.environ.get(cfg["env_client_id"], "")
        if client_id:
            available.append({
                "id": name,
                "name": name.capitalize(),
                "client_id": client_id,
            })
    return available


def build_oauth_authorize_url(provider: str, redirect_uri: str,
                               state: str) -> Optional[str]:
    """Build the OAuth authorization URL for the given provider."""
    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        return None
    client_id = os.environ.get(cfg["env_client_id"], "")
    if not client_id:
        return None

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": cfg["scopes"],
        "response_type": "code",
    }
    # Apple-specific: needs response_mode=form_post
    if cfg.get("response_mode"):
        params["response_mode"] = cfg["response_mode"]
    # Google: request offline access for refresh tokens
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"

    return cfg["authorize_url"] + "?" + urlencode(params)


async def exchange_oauth_code(provider: str, code: str,
                               redirect_uri: str) -> Optional[dict]:
    """Exchange an OAuth authorization code for user info.

    Returns a dict with keys: email, name, avatar_url, oauth_id
    or None on failure.
    """
    import httpx

    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        return None

    client_id = os.environ.get(cfg["env_client_id"], "")
    client_secret = os.environ.get(cfg["env_client_secret"], "")
    if not client_id or not client_secret:
        logger.error("OAuth %s: missing client credentials", provider)
        return None

    # Step 1: Exchange code for access token
    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            headers = {"Accept": "application/json"}
            resp = await client.post(
                cfg["token_url"], data=token_data, headers=headers,
            )
            resp.raise_for_status()
            token_resp = resp.json()
        except Exception as e:
            logger.error("OAuth %s token exchange failed: %s", provider, e)
            return None

        access_token = token_resp.get("access_token", "")
        if not access_token:
            logger.error("OAuth %s: no access_token in response", provider)
            return None

        # Step 2: Fetch user info
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        if provider == "apple":
            # Apple returns user info in the ID token (JWT)
            return _parse_apple_id_token(token_resp)

        try:
            resp = await client.get(cfg["userinfo_url"], headers=auth_headers)
            resp.raise_for_status()
            userinfo = resp.json()
        except Exception as e:
            logger.error("OAuth %s userinfo failed: %s", provider, e)
            return None

        return _extract_user_info(provider, userinfo, client, auth_headers)


async def _extract_user_info(provider: str, userinfo: dict,
                              client: object,
                              auth_headers: dict) -> Optional[dict]:
    """Extract normalized user info from provider-specific responses."""
    import httpx

    if provider == "github":
        email = userinfo.get("email") or ""
        # GitHub may not return email in profile; fetch from emails endpoint
        if not email:
            cfg = OAUTH_PROVIDERS["github"]
            email_url = cfg.get("userinfo_email_url", "")
            if email_url and isinstance(client, httpx.AsyncClient):
                try:
                    resp = await client.get(email_url, headers=auth_headers)
                    if resp.status_code == 200:
                        emails = resp.json()
                        for e in emails:
                            if e.get("primary") and e.get("verified"):
                                email = e["email"]
                                break
                        if not email and emails:
                            email = emails[0].get("email", "")
                except Exception:
                    pass
        return {
            "email": email,
            "name": userinfo.get("name") or userinfo.get("login", ""),
            "avatar_url": userinfo.get("avatar_url", ""),
            "oauth_id": str(userinfo.get("id", "")),
        }

    elif provider == "google":
        return {
            "email": userinfo.get("email", ""),
            "name": userinfo.get("name", ""),
            "avatar_url": userinfo.get("picture", ""),
            "oauth_id": str(userinfo.get("id", "")),
        }

    elif provider == "linkedin":
        return {
            "email": userinfo.get("email", ""),
            "name": userinfo.get("name", ""),
            "avatar_url": userinfo.get("picture", ""),
            "oauth_id": str(userinfo.get("sub", "")),
        }

    return None


def _parse_apple_id_token(token_resp: dict) -> Optional[dict]:
    """Parse Apple's ID token (JWT) to extract user info.

    We only decode the payload (base64) without full JWT verification here,
    since we received this token directly from Apple's token endpoint over HTTPS.
    """
    import base64

    id_token = token_resp.get("id_token", "")
    if not id_token:
        return None

    try:
        # JWT is header.payload.signature — we need the payload
        parts = id_token.split(".")
        if len(parts) < 2:
            return None
        # Add padding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return {
            "email": payload.get("email", ""),
            "name": payload.get("name", payload.get("email", "").split("@")[0]),
            "avatar_url": "",
            "oauth_id": payload.get("sub", ""),
        }
    except Exception as e:
        logger.error("Apple ID token parse error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Auth manager (high-level API)
# ---------------------------------------------------------------------------

class AuthManager:
    """High-level authentication operations.

    Used by the web server to handle login, registration, OAuth callbacks,
    and session validation.
    """

    def __init__(self, db: AuthDatabase) -> None:
        self.db = db

    def register(self, email: str, password: str,
                 display_name: str = "") -> tuple[User, str]:
        """Register a new user with email/password.

        Returns (user, token) tuple.
        Raises ValueError if email is taken or password is too short.
        """
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Valid email address required")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        user = self.db.create_user(
            email=email, display_name=display_name, password=password,
        )
        token = self.db.create_token(user.id)
        return user, token

    def login(self, email: str, password: str) -> tuple[User, str]:
        """Authenticate with email/password.

        Returns (user, token) tuple.
        Raises ValueError on invalid credentials.
        """
        email = email.strip().lower()
        user = self.db.get_user_by_email(email)
        if not user or not user.password_hash:
            raise ValueError("Invalid email or password")
        if not user.is_active:
            raise ValueError("Account is disabled")
        if not verify_password(password, user.password_hash):
            raise ValueError("Invalid email or password")

        self.db.update_last_login(user.id)
        token = self.db.create_token(user.id)
        return user, token

    def logout(self, token: str) -> None:
        """Revoke a single auth token."""
        self.db.revoke_token(token)

    def get_current_user(self, token: str) -> Optional[User]:
        """Validate a token and return the user, or None."""
        if not token:
            return None
        return self.db.validate_token(token)

    async def oauth_callback(self, provider: str, code: str,
                              redirect_uri: str) -> tuple[User, str]:
        """Handle OAuth callback: exchange code, find-or-create user.

        Returns (user, token) tuple.
        Raises ValueError on failure.
        """
        userinfo = await exchange_oauth_code(provider, code, redirect_uri)
        if not userinfo or not userinfo.get("email"):
            raise ValueError(f"Could not retrieve user info from {provider}")

        email = userinfo["email"].strip().lower()
        oauth_id = userinfo.get("oauth_id", "")

        # Try to find existing user by OAuth identity
        user = self.db.get_user_by_oauth(provider, oauth_id)
        if user:
            self.db.update_last_login(user.id)
            token = self.db.create_token(user.id)
            return user, token

        # Try to find existing user by email and link OAuth
        user = self.db.get_user_by_email(email)
        if user:
            self.db.link_oauth(
                user.id, provider, oauth_id,
                avatar_url=userinfo.get("avatar_url", ""),
            )
            self.db.update_last_login(user.id)
            token = self.db.create_token(user.id)
            return user, token

        # Create new user
        user = self.db.create_user(
            email=email,
            display_name=userinfo.get("name", ""),
            oauth_provider=provider,
            oauth_id=oauth_id,
            avatar_url=userinfo.get("avatar_url", ""),
        )
        token = self.db.create_token(user.id)
        return user, token
