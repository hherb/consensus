"""Data classes for users and auth tokens."""

import sqlite3
from dataclasses import dataclass

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


