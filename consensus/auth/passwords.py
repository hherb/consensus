"""Password hashing (PBKDF2-SHA256) and bearer-token hashing (SHA-256).

A stored password hash is ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``
so the iteration count can be raised without locking out existing users
(#127). The pre-#127 ``salt_hex$hash_hex`` format is still read and always
meant ``LEGACY_PASSWORD_HASH_ITERATIONS``.
"""

import functools
import hashlib
import hmac
import os
from typing import Optional

from .constants import (
    LEGACY_PASSWORD_HASH_ITERATIONS,
    PASSWORD_HASH_ITERATIONS,
    PASSWORD_HASH_SCHEME,
    PASSWORD_SALT_BYTES,
)

#: Fields in a current-format hash: scheme, iterations, salt, hash.
_HASH_FIELDS = 4
#: Fields in a legacy hash: salt, hash.
_LEGACY_HASH_FIELDS = 2
#: Placeholder password for the timing-equalising dummy hash (#131).
_DUMMY_PASSWORD = "consensus-timing-equaliser"


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-SHA256 of *password*."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations,
    )


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 at the current iteration count.

    Returns ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
    """
    salt = os.urandom(PASSWORD_SALT_BYTES)
    iterations = PASSWORD_HASH_ITERATIONS
    dk = _derive(password, salt, iterations)
    return f"{PASSWORD_HASH_SCHEME}${iterations}${salt.hex()}${dk.hex()}"


def _parse(stored: object) -> Optional[tuple[int, bytes, str]]:
    """Split a stored hash into ``(iterations, salt, hash_hex)``.

    Returns ``None`` for anything that is not a well-formed current or
    legacy hash.
    """
    if not isinstance(stored, str):
        return None
    fields = stored.split("$")
    try:
        if len(fields) == _HASH_FIELDS and fields[0] == PASSWORD_HASH_SCHEME:
            iterations = int(fields[1])
            salt_hex, hash_hex = fields[2], fields[3]
        elif len(fields) == _LEGACY_HASH_FIELDS:
            iterations = LEGACY_PASSWORD_HASH_ITERATIONS
            salt_hex, hash_hex = fields
        else:
            return None
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return None
    if iterations <= 0:
        return None
    return iterations, salt, hash_hex


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash (current or legacy format)."""
    parsed = _parse(stored)
    if parsed is None:
        return False
    iterations, salt, hash_hex = parsed
    return hmac.compare_digest(_derive(password, salt, iterations).hex(),
                               hash_hex)


def needs_rehash(stored: str) -> bool:
    """True when a well-formed hash is legacy or below the current cost."""
    parsed = _parse(stored)
    if parsed is None:
        return False
    is_legacy = not stored.startswith(PASSWORD_HASH_SCHEME + "$")
    return is_legacy or parsed[0] < PASSWORD_HASH_ITERATIONS


@functools.lru_cache(maxsize=None)
def _dummy_hash(iterations: int) -> str:
    """A throwaway hash at *iterations*, computed once per count."""
    salt = os.urandom(PASSWORD_SALT_BYTES)
    dk = _derive(_DUMMY_PASSWORD, salt, iterations)
    return f"{PASSWORD_HASH_SCHEME}${iterations}${salt.hex()}${dk.hex()}"


def burn_password_check(password: str) -> None:
    """Spend one current-cost PBKDF2 when there is no hash to check.

    Login calls this for an unknown or password-less email, so those
    attempts take as long as a wrong password (#131).
    """
    verify_password(password, _dummy_hash(PASSWORD_HASH_ITERATIONS))


def _hash_token(token: str) -> str:
    """Hash a bearer token for storage (SHA-256)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
