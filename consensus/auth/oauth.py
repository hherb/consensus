"""OAuth Authorization Code flow: provider config, URLs and code exchange."""

import asyncio
import json
import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from ..http_fetch import is_retryable_status
from .constants import (
    OAUTH_HTTP_TIMEOUT,
    OAUTH_MAX_RETRIES,
    OAUTH_PROVIDERS,
    OAUTH_RETRY_BASE_DELAY,
)

logger = logging.getLogger(__name__)

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


async def _send_with_retry(client: httpx.AsyncClient, method: str, url: str,
                           **kwargs: object) -> httpx.Response:
    """Send one request to a provider, retrying transient faults.

    Transport errors, timeouts and retryable statuses
    (:func:`http_fetch.is_retryable_status`) are attempted up to
    ``OAUTH_MAX_RETRIES`` times in total, with exponential backoff. Retrying
    the code-exchange POST is safe: if an earlier attempt did reach the
    provider, the single-use code is spent and the retry fails permanently.

    Returns:
        The first non-retryable response, or the final attempt's; the caller
        checks its status.

    Raises:
        httpx.TransportError: When the final attempt fails in transport.
    """
    for attempt in range(OAUTH_MAX_RETRIES - 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.TransportError as e:
            fault = type(e).__name__
        else:
            if not is_retryable_status(resp.status_code):
                return resp
            fault = f"HTTP {resp.status_code}"
        delay = OAUTH_RETRY_BASE_DELAY * (2 ** attempt)
        logger.warning("OAuth request to %s failed (attempt %d/%d: %s), "
                       "retrying in %.1fs", url, attempt + 1,
                       OAUTH_MAX_RETRIES, fault, delay)
        await asyncio.sleep(delay)
    return await client.request(method, url, **kwargs)


async def exchange_oauth_code(provider: str, code: str,
                               redirect_uri: str) -> Optional[dict]:
    """Exchange an OAuth authorization code for user info.

    Returns a dict with keys: email, name, avatar_url, oauth_id
    or None on failure.
    """
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

    async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT) as client:
        try:
            headers = {"Accept": "application/json"}
            resp = await _send_with_retry(
                client, "POST", cfg["token_url"], data=token_data,
                headers=headers,
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
            resp = await _send_with_retry(
                client, "GET", cfg["userinfo_url"], headers=auth_headers,
            )
            resp.raise_for_status()
            userinfo = resp.json()
        except Exception as e:
            logger.error("OAuth %s userinfo failed: %s", provider, e)
            return None

        return await _extract_user_info(provider, userinfo, client, auth_headers)


def _coerce_verified(value: object) -> bool:
    """Coerce a provider's email-verified flag (bool or string) to bool.

    Providers report this inconsistently — Google/LinkedIn send a JSON
    boolean while Apple sends the string ``"true"``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


async def _extract_user_info(provider: str, userinfo: dict,
                              client: httpx.AsyncClient,
                              auth_headers: dict[str, str]) -> Optional[dict]:
    """Extract normalized user info from provider-specific responses.

    Args:
        provider: OAuth provider name (e.g. "github", "google").
        userinfo: Raw user info dict from the provider's API.
        client: Active httpx client for follow-up requests (e.g. GitHub emails).
        auth_headers: Authorization headers with Bearer token.

    Returns:
        Normalized dict with keys: email, name, avatar_url, oauth_id.
        None if the provider is unrecognized.
    """
    if provider == "github":
        email = userinfo.get("email") or ""
        # The profile email field's verification status is unknown, so
        # confirm it against the emails endpoint, which carries a per-email
        # ``verified`` flag.  Default to unverified.
        email_verified = False
        cfg = OAUTH_PROVIDERS["github"]
        email_url = cfg.get("userinfo_email_url", "")
        if email_url:
            try:
                resp = await _send_with_retry(
                    client, "GET", email_url, headers=auth_headers,
                )
                if resp.status_code != 200:
                    logger.warning("GitHub email lookup failed: HTTP %d; "
                                   "treating the email as unverified",
                                   resp.status_code)
                else:
                    emails = resp.json()
                    for e in emails:
                        if e.get("primary") and e.get("verified"):
                            email = e["email"]
                            email_verified = True
                            break
                    if not email and emails:
                        email = emails[0].get("email", "")
                        email_verified = bool(emails[0].get("verified"))
                    elif email and not email_verified:
                        # Reconcile the profile email with the endpoint's
                        # verified flag for that address.
                        for e in emails:
                            if e.get("email") == email:
                                email_verified = bool(e.get("verified"))
                                break
            except Exception as e:
                logger.warning("GitHub email lookup failed: %s; treating "
                               "the email as unverified", type(e).__name__)
        return {
            "email": email,
            "name": userinfo.get("name") or userinfo.get("login", ""),
            "avatar_url": userinfo.get("avatar_url", ""),
            "oauth_id": str(userinfo.get("id", "")),
            "email_verified": email_verified,
        }

    elif provider == "google":
        return {
            "email": userinfo.get("email", ""),
            "name": userinfo.get("name", ""),
            "avatar_url": userinfo.get("picture", ""),
            "oauth_id": str(userinfo.get("id", "")),
            "email_verified": _coerce_verified(
                userinfo.get("email_verified", userinfo.get("verified_email"))
            ),
        }

    elif provider == "linkedin":
        return {
            "email": userinfo.get("email", ""),
            "name": userinfo.get("name", ""),
            "avatar_url": userinfo.get("picture", ""),
            "oauth_id": str(userinfo.get("sub", "")),
            "email_verified": _coerce_verified(userinfo.get("email_verified")),
        }

    return None


def _parse_apple_id_token(token_resp: dict) -> Optional[dict]:
    """Parse Apple's ID token (JWT) to extract user info.

    We only decode the payload (base64) without full JWT verification here,
    since we received this token directly from Apple's token endpoint over HTTPS.

    TODO: For production use, verify the JWT signature against Apple's public
    keys (https://appleid.apple.com/auth/keys) per Apple's documentation.
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
        # Add base64 padding (0–2 '=' chars as needed)
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return {
            "email": payload.get("email", ""),
            "name": payload.get("name", payload.get("email", "").split("@")[0]),
            "avatar_url": "",
            "oauth_id": payload.get("sub", ""),
            "email_verified": _coerce_verified(payload.get("email_verified")),
        }
    except Exception as e:
        logger.error("Apple ID token parse error: %s", e)
        return None


