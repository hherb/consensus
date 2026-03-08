"""Fetch and cache model pricing from OpenRouter."""

import logging
import sqlite3
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week

# Map base_url hostnames to OpenRouter provider prefixes for disambiguation
_HOST_TO_PROVIDER = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
    "api.mistral.ai": "mistralai",
    "api.groq.com": "groq",
    "api.deepseek.com": "deepseek",
    "api.together.xyz": "together",
    "openrouter.ai": "openrouter",
}


class PricingCache:
    """Fetch model pricing from OpenRouter and cache in SQLite."""

    def __init__(self, conn: sqlite3.Connection,
                 lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def _needs_refresh(self) -> bool:
        """Check if pricing data is stale (> 1 week old) or empty."""
        cur = self._conn.execute(
            "SELECT MIN(last_updated) FROM model_pricing"
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return True
        return (time.time() - row[0]) > PRICING_MAX_AGE_SECONDS

    def needs_refresh_for_model(self, model_name: str) -> bool:
        """Check if a specific model has no pricing data."""
        cur = self._conn.execute(
            "SELECT 1 FROM model_pricing WHERE model_id LIKE ?",
            (f"%/{model_name}",),
        )
        return cur.fetchone() is None

    def refresh(self) -> bool:
        """Fetch pricing from OpenRouter and update cache.

        Returns True if successful, False on failure.
        """
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(OPENROUTER_MODELS_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.warning("Failed to fetch pricing from OpenRouter",
                           exc_info=True)
            return False

        models = data.get("data", [])
        if not models:
            logger.warning("OpenRouter returned no models")
            return False

        now = time.time()
        rows = []
        for m in models:
            model_id = m.get("id", "")
            pricing = m.get("pricing") or {}
            prompt_cost = _parse_cost(pricing.get("prompt"))
            completion_cost = _parse_cost(pricing.get("completion"))
            if model_id and (prompt_cost or completion_cost):
                rows.append((model_id, prompt_cost, completion_cost, now))

        if not rows:
            return False

        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO model_pricing "
                "(model_id, prompt_cost, completion_cost, last_updated) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

        logger.info("Updated pricing for %d models from OpenRouter",
                     len(rows))
        return True

    def lookup(self, model_name: str,
               base_url: str = "") -> Optional[tuple[float, float]]:
        """Look up (prompt_cost, completion_cost) for a model name.

        Uses fuzzy matching: strips provider/ prefix from OpenRouter IDs.
        If base_url is provided, uses it to disambiguate.
        Returns None if no match found.
        """
        # Strip date suffixes (e.g. "claude-opus-4-6-20250605" → "claude-opus-4-6")
        model_name = _strip_date_suffix(model_name)

        # Try exact match first (user might use full OpenRouter ID)
        cur = self._conn.execute(
            "SELECT prompt_cost, completion_cost FROM model_pricing "
            "WHERE model_id = ?",
            (model_name,),
        )
        row = cur.fetchone()
        if row:
            return (row[0], row[1])

        # Build list of name variants to try (handles hyphen vs dot in
        # version suffixes, e.g. "claude-opus-4-6" → "claude-opus-4.6")
        variants = [model_name] + _version_variants(model_name)

        matches = []
        for name in variants:
            cur = self._conn.execute(
                "SELECT model_id, prompt_cost, completion_cost "
                "FROM model_pricing WHERE model_id LIKE ?",
                (f"%/{name}",),
            )
            matches = cur.fetchall()
            if matches:
                break

        if not matches:
            # Try prefix matching (e.g. "gpt-4o" matches "openai/gpt-4o-2024-08-06")
            for name in variants:
                cur = self._conn.execute(
                    "SELECT model_id, prompt_cost, completion_cost "
                    "FROM model_pricing WHERE model_id LIKE ?",
                    (f"%/{name}%",),
                )
                matches = cur.fetchall()
                if matches:
                    break

        if not matches:
            return None

        if len(matches) == 1:
            return (matches[0][1], matches[0][2])

        # Disambiguate using base_url
        if base_url:
            preferred = _provider_from_url(base_url)
            if preferred:
                for m in matches:
                    if m[0].startswith(preferred + "/"):
                        return (m[1], m[2])

        # Fall back to first match
        return (matches[0][1], matches[0][2])

    def calculate_cost(self, model_name: str, base_url: str,
                       prompt_tokens: int,
                       completion_tokens: int) -> Optional[float]:
        """Calculate cost in USD for a given request.

        Returns None if pricing not available for this model.
        """
        pricing = self.lookup(model_name, base_url)
        if pricing is None:
            return None
        prompt_cost, completion_cost = pricing
        return (prompt_tokens * prompt_cost) + \
               (completion_tokens * completion_cost)


def _strip_date_suffix(model_name: str) -> str:
    """Strip trailing date suffixes like -20250605 from model names."""
    import re
    return re.sub(r'-\d{8}$', '', model_name)


def _version_variants(model_name: str) -> list[str]:
    """Generate naming variants for version suffixes.

    Anthropic's API returns hyphens (claude-opus-4-6) while OpenRouter
    uses dots (claude-opus-4.6). Generate the alternate form.
    """
    import re
    # "claude-opus-4-6" → "claude-opus-4.6"
    dot_variant = re.sub(r'-(\d+)$', r'.\1', model_name)
    # "claude-opus-4.6" → "claude-opus-4-6"
    hyphen_variant = re.sub(r'\.(\d+)$', r'-\1', model_name)
    variants = []
    if dot_variant != model_name:
        variants.append(dot_variant)
    if hyphen_variant != model_name:
        variants.append(hyphen_variant)
    return variants


def _parse_cost(value: object) -> float:
    """Parse a cost value from OpenRouter (string or number)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _provider_from_url(base_url: str) -> str:
    """Extract OpenRouter provider prefix from a base URL."""
    host = urlparse(base_url).hostname or ""
    for pattern, provider in _HOST_TO_PROVIDER.items():
        if pattern in host:
            return provider
    return ""
