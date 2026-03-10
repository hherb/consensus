"""Fetch and cache model pricing from OpenRouter."""

import logging
import re
import sqlite3
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week

# Aliases: provider model names that differ from OpenRouter's naming
# Format: "provider-model-name" → "openrouter/model-id"
_MODEL_ALIASES = {
    "deepseek-reasoner": "deepseek/deepseek-r1",
    "deepseek-chat": "deepseek/deepseek-chat",
    "mistral-large-latest": "mistralai/mistral-large",
    "mistral-medium-latest": "mistralai/mistral-medium-3",
    "mistral-small-latest": "mistralai/mistral-small-3.2-24b-instruct",
    "codestral-latest": "mistralai/codestral-2508",
    "open-mistral-nemo": "mistralai/mistral-nemo",
}

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
            arch = m.get("architecture") or {}
            input_mods = arch.get("input_modalities") or ["text"]
            input_modalities = ",".join(input_mods)
            if model_id:
                rows.append((
                    model_id, prompt_cost, completion_cost, now,
                    input_modalities,
                ))

        if not rows:
            return False

        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO model_pricing "
                "(model_id, prompt_cost, completion_cost, last_updated,"
                " input_modalities) "
                "VALUES (?, ?, ?, ?, ?)",
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
        row = self._lookup_row(model_name, base_url)
        if row is None:
            return None
        return (row["prompt_cost"], row["completion_cost"])

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

    def calculate_cost_with_refresh(
        self, model_name: str, base_url: str,
        prompt_tokens: int, completion_tokens: int,
    ) -> Optional[float]:
        """Calculate cost in USD, refreshing pricing data once if model is unknown.

        Wraps ``calculate_cost`` with a single retry after refreshing the
        pricing cache when the model is not yet known.

        Args:
            model_name: The model identifier (e.g. "gpt-4o").
            base_url: The provider's API base URL for disambiguation.
            prompt_tokens: Number of prompt/input tokens used.
            completion_tokens: Number of completion/output tokens used.

        Returns:
            Cost in USD, or None if pricing is unavailable even after refresh.
        """
        cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
        if cost is None and self.needs_refresh_for_model(model_name):
            self.refresh()
            cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
        return cost

    def has_input_modality(self, model_name: str, modality: str,
                           base_url: str = "") -> Optional[bool]:
        """Check if a model supports a given input modality (e.g. "image").

        Uses the same fuzzy matching logic as ``lookup()``.
        Refreshes the cache once if the model is unknown.
        Returns True/False if the model is found, None if still unknown.
        """
        row = self._lookup_row(model_name, base_url)
        if row is None and self.needs_refresh_for_model(model_name):
            self.refresh()
            row = self._lookup_row(model_name, base_url)
        if row is None:
            return None
        modalities = (row["input_modalities"] or "text").lower().split(",")
        return modality.lower() in modalities

    def _lookup_row(self, model_name: str,
                    base_url: str = "") -> Optional[dict]:
        """Look up the full model_pricing row for a model name.

        Returns a dict with model_id, prompt_cost, completion_cost,
        input_modalities — or None if not found.
        """
        model_name = _strip_date_suffix(model_name)

        alias = _MODEL_ALIASES.get(model_name)
        if alias:
            cur = self._conn.execute(
                "SELECT model_id, prompt_cost, completion_cost, "
                "input_modalities FROM model_pricing WHERE model_id = ?",
                (alias,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

        cur = self._conn.execute(
            "SELECT model_id, prompt_cost, completion_cost, "
            "input_modalities FROM model_pricing WHERE model_id = ?",
            (model_name,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        variants = [model_name] + _version_variants(model_name)

        matches = []
        for name in variants:
            cur = self._conn.execute(
                "SELECT model_id, prompt_cost, completion_cost, "
                "input_modalities FROM model_pricing "
                "WHERE model_id LIKE ?",
                (f"%/{name}",),
            )
            matches = cur.fetchall()
            if matches:
                break

        if not matches:
            for name in variants:
                cur = self._conn.execute(
                    "SELECT model_id, prompt_cost, completion_cost, "
                    "input_modalities FROM model_pricing "
                    "WHERE model_id LIKE ?",
                    (f"%/{name}%",),
                )
                matches = cur.fetchall()
                if matches:
                    break

        if not matches:
            return None

        if len(matches) == 1:
            return dict(matches[0])

        if base_url:
            preferred = _provider_from_url(base_url)
            if preferred:
                for m in matches:
                    if m["model_id"].startswith(preferred + "/"):
                        return dict(m)

        return dict(matches[0])


def _strip_date_suffix(model_name: str) -> str:
    """Strip trailing date suffixes like -20250605 from model names."""
    return re.sub(r'-\d{8}$', '', model_name)


def _version_variants(model_name: str) -> list[str]:
    """Generate naming variants for version suffixes.

    Anthropic's API returns hyphens (claude-opus-4-6) while OpenRouter
    uses dots (claude-opus-4.6). Generate the alternate form.
    """
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
