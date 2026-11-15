"""High-level authentication operations used by the web server."""

import logging
import sqlite3
from typing import Optional

from .constants import MIN_PASSWORD_LENGTH
from .database import AuthDatabase
from .models import User
from .oauth import _coerce_verified, exchange_oauth_code
from .passwords import burn_password_check, needs_rehash, verify_password

logger = logging.getLogger(__name__)


def _lookup_failed_message(provider: str) -> str:
    """User-facing message for a provider whose email lookup failed."""
    return (f"Could not confirm your email with {provider} right now.  "
            "Please try again in a moment.")


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
        Raises ValueError if the email is invalid or taken, or the password
        is too short.
        """
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Valid email address required")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH}"
                             " characters")

        user = self.db.create_user(
            email=email, display_name=display_name, password=password,
        )
        token = self.db.create_token(user.id)
        return user, token

    def login(self, email: str, password: str) -> tuple[User, str]:
        """Authenticate with email/password.

        Returns (user, token) tuple.
        Raises ValueError on invalid credentials. An unknown or
        password-less email costs one current-cost PBKDF2, as a wrong
        password on an up-to-date hash does, so timing does not reveal
        which emails have a password (#131). A hash below the current cost
        is re-hashed after a successful login of an active user (#127).
        """
        email = email.strip().lower()
        user = self.db.get_user_by_email(email)
        if not user or not user.password_hash:
            burn_password_check(password)  # cost of a wrong password
            raise ValueError("Invalid email or password")
        if not verify_password(password, user.password_hash):
            raise ValueError("Invalid email or password")
        if not user.is_active:
            raise ValueError("Account is disabled")
        if needs_rehash(user.password_hash):
            self._rehash(user, password)

        self.db.update_last_login(user.id)
        token = self.db.create_token(user.id)
        return user, token

    def _rehash(self, user: User, password: str) -> None:
        """Upgrade *user*'s hash to the current cost; never fails a login.

        Compare-and-swap on the hash just verified, so a concurrent
        lock-out or password change wins. A failed write is logged and
        retried at the next login.
        """
        try:
            self.db.change_password(user.id, password,
                                    expected=user.password_hash)
        except sqlite3.Error:
            logger.exception("Password rehash failed for user %s; "
                             "will retry at the next login", user.id)

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
        # Without an id, the identity lookup would match any other account
        # stored with an empty id.
        if (not userinfo or not userinfo.get("email")
                or not userinfo.get("oauth_id")):
            if userinfo:
                logger.warning("OAuth %s returned no email or user id",
                               provider)
                if userinfo.get("email_lookup_failed"):
                    raise ValueError(_lookup_failed_message(provider))
            raise ValueError(f"Could not retrieve user info from {provider}")

        email = userinfo["email"].strip().lower()
        oauth_id = userinfo.get("oauth_id", "")

        # Try to find existing user by OAuth identity
        user = self.db.get_user_by_oauth(provider, oauth_id)
        if user:
            self.db.update_last_login(user.id)
            token = self.db.create_token(user.id)
            return user, token

        # Try to find existing user by email and link OAuth.  Only auto-link
        # when the provider asserts the email is verified — otherwise an
        # attacker controlling a provider account with the victim's email
        # could take over an existing (e.g. password) account.
        user = self.db.get_user_by_email(email)
        if user:
            if not _coerce_verified(userinfo.get("email_verified")):
                # A failed lookup says nothing about verification.
                if userinfo.get("email_lookup_failed"):
                    raise ValueError(_lookup_failed_message(provider))
                raise ValueError(
                    f"An account already exists for {email}, but {provider} "
                    "did not confirm this email is verified.  Sign in with "
                    "your existing method instead."
                )
            # Registration never verifies the email, so a password may have
            # been set by someone who does not own the address; the provider
            # just proved who does.  Lock that someone out, in the same
            # transaction as the link.
            had_password = bool(user.password_hash)
            self.db.link_oauth(
                user.id, provider, oauth_id,
                avatar_url=userinfo.get("avatar_url", ""),
                lock_out_password=had_password,
            )
            if had_password:
                logger.warning(
                    "OAuth %s verified the email of password account %d; "
                    "cleared its password and revoked its sessions",
                    provider, user.id)
            self.db.update_last_login(user.id)
            token = self.db.create_token(user.id)
            return self.db.get_user_by_id(user.id) or user, token

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
