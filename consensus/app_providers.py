"""Provider management — CRUD operations and key redaction.

Pure functions that operate on a Database instance and return
frontend-safe dicts. No dependency on ConsensusApp.
"""

from typing import Callable, Optional

from .ai_client import AIClient
from .config import has_api_key, remove_api_key, save_api_key
from .database import Database

# Sentinel value for key removal in update_provider
_REMOVE_KEY_SENTINEL = "__REMOVE__"


def provider_for_frontend(
    provider: Optional[dict],
    request_api_keys: dict[str, str],
) -> Optional[dict]:
    """Redact secrets before sending provider data to the frontend.

    Args:
        provider: Raw provider dict from the database, or None.
        request_api_keys: Per-request BYOK keys mapping provider_id -> key.

    Returns:
        A copy of the provider dict with ``api_key_env`` removed and
        ``has_key`` set to True if a key is available via env or BYOK.
        Returns None if provider is None.
    """
    if not provider:
        return None
    p = dict(provider)
    env_var = p.get("api_key_env") or ""
    provider_id = p.get("id", 0)
    has_env = has_api_key(env_var)
    has_byok = bool(request_api_keys.get(str(provider_id), ""))
    p["has_key"] = has_env or has_byok
    p.pop("api_key_env", None)
    return p


def add_provider(
    db: Database,
    name: str,
    base_url: str,
    api_key_env: str = "",
    api_key: str = "",
    request_api_keys: Optional[dict[str, str]] = None,
) -> Optional[dict]:
    """Add a new API provider and return its frontend-safe data.

    If *api_key* is provided alongside *api_key_env*, the key is saved
    to the env file and only the env var name is stored in the database.

    Args:
        db: Database instance.
        name: Display name for the provider.
        base_url: The provider's API base URL.
        api_key_env: Environment variable name for the API key.
        api_key: The actual API key value (saved to env file if provided).
        request_api_keys: Per-request BYOK keys for frontend redaction.

    Returns:
        Frontend-safe provider dict, or None on failure.
    """
    if api_key and api_key_env:
        save_api_key(api_key_env, api_key)
    pid = db.add_provider(name, base_url, api_key_env)
    return provider_for_frontend(db.get_provider(pid), request_api_keys or {})


def update_provider(
    db: Database,
    provider_id: int,
    api_key: str = "",
    **kwargs: object,
) -> bool:
    """Update an existing provider's fields.

    If *api_key* is a non-empty string, the key is saved. If *api_key*
    is the sentinel ``"__REMOVE__"``, the stored key is deleted.

    Args:
        db: Database instance.
        provider_id: ID of the provider to update.
        api_key: New key value, removal sentinel, or empty to skip.
        **kwargs: Fields to update (name, base_url, api_key_env, etc.).

    Returns:
        True if the provider was found and updated, False otherwise.
    """
    provider = db.get_provider(provider_id)
    if not provider:
        return False
    env_var = kwargs.get("api_key_env") or provider["api_key_env"]
    if api_key == _REMOVE_KEY_SENTINEL and env_var:
        remove_api_key(env_var)
    elif api_key and env_var:
        save_api_key(env_var, api_key)
    db.update_provider(provider_id, **kwargs)
    return True


def delete_provider(db: Database, provider_id: int) -> bool:
    """Delete a provider by ID.

    Args:
        db: Database instance.
        provider_id: ID of the provider to delete.

    Returns:
        True (always succeeds).
    """
    db.delete_provider(provider_id)
    return True


def get_providers(
    db: Database,
    request_api_keys: dict[str, str],
) -> list[dict]:
    """Return all configured providers with secrets redacted.

    Args:
        db: Database instance.
        request_api_keys: Per-request BYOK keys for frontend redaction.

    Returns:
        List of frontend-safe provider dicts.
    """
    return [
        provider_for_frontend(p, request_api_keys)
        for p in db.get_providers()
    ]


async def fetch_models(
    db: Database,
    provider_id: int,
    resolve_key_fn: Callable[[int, str], str],
) -> list[str]:
    """Fetch available models from a provider's API.

    Args:
        db: Database instance.
        provider_id: ID of the provider to query.
        resolve_key_fn: Callable(provider_id, api_key_env) -> str that
            resolves the API key from env or BYOK.

    Returns:
        List of model name strings, or empty list if provider not found.
    """
    provider = db.get_provider(provider_id)
    if not provider:
        return []
    api_key = resolve_key_fn(provider_id, provider["api_key_env"] or "")
    async with AIClient(provider["base_url"], api_key) as client:
        return await client.list_models()
