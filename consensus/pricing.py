"""Fetch and cache model pricing from OpenRouter."""

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from .http_fetch import fetch_capped

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week
#: Per-request timeout for the catalogue download, in seconds.
PRICING_FETCH_TIMEOUT = 30.0
#: Largest catalogue accepted (it is a few MB today).
PRICING_MAX_BYTES = 32 * 1024 * 1024

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

# Column list for model_pricing row lookups (see _lookup_row)
_ROW_COLUMNS = ("model_id, prompt_cost, completion_cost, "
                "input_modalities, context_length, supported_parameters")

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
        # One capability refresh per process: a failed attempt (e.g.
        # offline) must not re-block every supports_tools() call.
        self._capability_refresh_attempted = False
        # Unknown models already refreshed for, so each costs one download.
        self._unknown_models_refreshed: set[str] = set()
        # The one download in flight, if any (see refresh_in_background).
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_thread_lock = threading.Lock()

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

    def _refresh_for_unknown_model(self, model_name: str) -> None:
        """Start a background refresh at most once per model for this cache
        (one per ``Database``, so per ``ConsensusApp``) for a model it does
        not know.

        The lookup that triggers it still reports the model as unknown;
        lookups after the download lands see it. A model OpenRouter does
        not list (a local model, say) is still unknown after the refresh;
        without this guard every message priced for it re-downloaded the
        whole catalogue.
        shortcut: a model OpenRouter lists only later, or whose one refresh
        failed after its retries, stays unpriced for this cache's lifetime;
        add a retry interval if that matters.
        """
        if (model_name in self._unknown_models_refreshed
                or not self.needs_refresh_for_model(model_name)):
            return
        self._unknown_models_refreshed.add(model_name)
        self.refresh_in_background()

    def refresh_in_background(self) -> Optional[threading.Thread]:
        """Run :meth:`refresh` in a daemon thread, unless one is running.

        Every lookup is reached from an event loop, and a download there
        stalls every request for as long as the network takes (#115). The
        running download fetches the whole catalogue, so it also covers any
        model asked for while it is in flight.

        Returns:
            The started thread, or ``None`` if a download was in flight.
        """
        with self._refresh_thread_lock:
            if self._refresh_thread and self._refresh_thread.is_alive():
                return None
            self._refresh_thread = threading.Thread(
                target=self.refresh, name="pricing-refresh", daemon=True)
            self._refresh_thread.start()
            return self._refresh_thread

    def refresh(self) -> bool:
        """Fetch pricing from OpenRouter and update cache.

        Blocks for the download, retries included, so call it off the event
        loop: :meth:`refresh_in_background` does.

        Returns True if successful, False on failure.
        """
        try:
            body, _ = asyncio.run(fetch_capped(
                OPENROUTER_MODELS_URL, max_bytes=PRICING_MAX_BYTES,
                timeout=PRICING_FETCH_TIMEOUT))
            models = json.loads(body).get("data", [])
        except Exception:
            logger.warning("Failed to fetch pricing from OpenRouter",
                           exc_info=True)
            return False

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
            context_length = m.get("context_length")
            supported_parameters = ",".join(
                m.get("supported_parameters") or [])
            if model_id:
                rows.append((
                    model_id, prompt_cost, completion_cost, now,
                    input_modalities, context_length, supported_parameters,
                ))

        if not rows:
            return False

        try:
            with self._lock:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO model_pricing "
                    "(model_id, prompt_cost, completion_cost, last_updated,"
                    " input_modalities, context_length,"
                    " supported_parameters) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._conn.commit()
        except sqlite3.Error:
            # E.g. a --multi-user session expired, closing its DB, mid-fetch.
            logger.warning("Failed to store OpenRouter pricing",
                           exc_info=True)
            return False

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
        """Calculate cost in USD; an unknown model starts a background refresh.

        Wraps ``calculate_cost``: when the model is not yet known, the
        pricing cache is refreshed in the background (once per model), so
        later calls can price it.

        Args:
            model_name: The model identifier (e.g. "gpt-4o").
            base_url: The provider's API base URL for disambiguation.
            prompt_tokens: Number of prompt/input tokens used.
            completion_tokens: Number of completion/output tokens used.

        Returns:
            Cost in USD, or None if pricing is unavailable (yet).
        """
        cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
        if cost is None:
            self._refresh_for_unknown_model(model_name)
        return cost

    def has_input_modality(self, model_name: str, modality: str,
                           base_url: str = "") -> Optional[bool]:
        """Check if a model supports a given input modality (e.g. "image").

        Uses the same fuzzy matching logic as ``lookup()``.
        Refreshes the cache in the background once if the model is unknown.
        Returns True/False if the model is found, None if still unknown.
        """
        row = self._lookup_row(model_name, base_url)
        if row is None:
            self._refresh_for_unknown_model(model_name)
            return None
        modalities = (row["input_modalities"] or "text").lower().split(",")
        return modality.lower() in modalities

    def get_context_length(self, model_name: str,
                           base_url: str = "") -> Optional[int]:
        """Return the context window size (in tokens) for a model, or None.

        Uses the same fuzzy matching logic as ``lookup()``.
        Refreshes the cache in the background once if the model is unknown.
        """
        row = self._lookup_row(model_name, base_url)
        if row is None:
            self._refresh_for_unknown_model(model_name)
            return None
        return row.get("context_length")

    def supports_tools(self, model_name: str,
                       base_url: str = "") -> Optional[bool]:
        """Check if a model supports native tool/function calling.

        Uses the same fuzzy matching logic as ``lookup()`` and refreshes
        the cache in the background once if the model is unknown, or (at
        most once per process) when the whole cache predates the
        ``supported_parameters`` column (migration 014) so the setup
        gate works soon after an upgrade.  Returns True/False when
        OpenRouter reports the model's supported parameters, or None
        when the model — or its capability data — is unknown (e.g.
        local models).
        """
        row = self._lookup_row(model_name, base_url)
        if row is None:
            self._refresh_for_unknown_model(model_name)
            return None
        params = (row.get("supported_parameters") or "").strip()
        if (not params and not self._capability_refresh_attempted
                and self._capability_data_missing()):
            self._capability_refresh_attempted = True
            self.refresh_in_background()
        if not params:
            return None
        return "tools" in params.split(",")

    def _capability_data_missing(self) -> bool:
        """True when no cached row carries capability data.

        This is the signature of a cache populated before migration 014
        added ``supported_parameters``; after any successful refresh the
        column is populated and this check stays cheap and False.
        """
        cur = self._conn.execute(
            "SELECT 1 FROM model_pricing "
            "WHERE IFNULL(supported_parameters, '') != '' LIMIT 1"
        )
        return cur.fetchone() is None

    def _lookup_row(self, model_name: str,
                    base_url: str = "") -> Optional[dict]:
        """Look up the full model_pricing row for a model name.

        Returns a dict with model_id, prompt_cost, completion_cost,
        input_modalities, context_length — or None if not found.
        """
        model_name = _strip_date_suffix(model_name)

        alias = _MODEL_ALIASES.get(model_name)
        if alias:
            cur = self._conn.execute(
                f"SELECT {_ROW_COLUMNS} FROM model_pricing "
                "WHERE model_id = ?",
                (alias,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

        cur = self._conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM model_pricing "
            "WHERE model_id = ?",
            (model_name,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        variants = [model_name] + _version_variants(model_name)

        matches = []
        for name in variants:
            cur = self._conn.execute(
                f"SELECT {_ROW_COLUMNS} FROM model_pricing "
                "WHERE model_id LIKE ?",
                (f"%/{name}",),
            )
            matches = cur.fetchall()
            if matches:
                break

        if not matches:
            for name in variants:
                cur = self._conn.execute(
                    f"SELECT {_ROW_COLUMNS} FROM model_pricing "
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

        # Multiple candidates and the provider did not disambiguate.  If they
        # all agree on price the ambiguity is harmless; otherwise refuse to
        # guess rather than report a materially wrong cost.
        prices = {
            (m["prompt_cost"], m["completion_cost"]) for m in matches
        }
        if len(prices) == 1:
            return dict(matches[0])
        logger.warning(
            "Ambiguous pricing for %r: %d candidates with differing prices; "
            "cost unavailable.", model_name, len(matches),
        )
        return None


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
