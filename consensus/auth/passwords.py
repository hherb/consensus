"""Password hashing (PBKDF2-SHA256) and bearer-token hashing (SHA-256)."""

import hashlib
import hmac
import os

from .constants import PASSWORD_HASH_ITERATIONS, PASSWORD_SALT_BYTES

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


