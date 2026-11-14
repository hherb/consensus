"""Tests for consensus.pricing — PricingCache cost calculation."""

import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

from consensus.pricing import PricingCache


@pytest.fixture
def pricing_cache(tmp_path):
    """Create a PricingCache with a temporary database matching production schema."""
    db_path = str(tmp_path / "pricing_test.db")
    # Shared with the refresh thread, as Database's connection is.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_pricing "
        "(model_id TEXT PRIMARY KEY, prompt_cost REAL NOT NULL DEFAULT 0, "
        "completion_cost REAL NOT NULL DEFAULT 0, last_updated REAL NOT NULL DEFAULT 0, "
        "input_modalities TEXT DEFAULT 'text', context_length INTEGER, "
        "supported_parameters TEXT DEFAULT '')"
    )
    conn.commit()
    cache = PricingCache(conn, threading.Lock())
    return cache


def _settle(cache: PricingCache) -> None:
    """Wait for the background refresh a lookup started, if any."""
    if cache._refresh_thread is not None:
        cache._refresh_thread.join()


class TestCalculateCostWithRefresh:
    """Tests for the calculate_cost_with_refresh convenience method."""

    def test_returns_cost_when_model_known(self, pricing_cache):
        """Should return cost directly without refreshing if model is known."""
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, last_updated) "
            "VALUES (?, ?, ?, ?)",
            ("test-model", 0.001, 0.002, time.time()),
        )
        pricing_cache._conn.commit()

        cost = pricing_cache.calculate_cost_with_refresh(
            "test-model", "", 100, 50,
        )
        assert cost is not None
        assert abs(cost - (100 * 0.001 + 50 * 0.002)) < 1e-10

    def test_ambiguous_differing_prices_returns_none(self, pricing_cache):
        """Two providers with the same model name but different prices is
        ambiguous; refuse to guess rather than report a wrong cost."""
        now = time.time()
        pricing_cache._conn.executemany(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, "
            "last_updated) VALUES (?, ?, ?, ?)",
            [
                ("providera/shared-model", 0.001, 0.002, now),
                ("providerb/shared-model", 0.010, 0.020, now),
            ],
        )
        pricing_cache._conn.commit()
        assert pricing_cache.lookup("shared-model") is None

    def test_ambiguous_same_price_returns_match(self, pricing_cache):
        """When ambiguous candidates agree on price, the match is returned."""
        now = time.time()
        pricing_cache._conn.executemany(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, "
            "last_updated) VALUES (?, ?, ?, ?)",
            [
                ("providera/same-model", 0.001, 0.002, now),
                ("providerb/same-model", 0.001, 0.002, now),
            ],
        )
        pricing_cache._conn.commit()
        assert pricing_cache.lookup("same-model") == (0.001, 0.002)

    def test_returns_none_when_model_unknown_and_refresh_fails(self, pricing_cache):
        """Should return None if model unknown and refresh doesn't help."""
        with patch.object(pricing_cache, "refresh", return_value=False) as mock_refresh:
            cost = pricing_cache.calculate_cost_with_refresh(
                "unknown-model", "", 100, 50,
            )
            _settle(pricing_cache)
        mock_refresh.assert_called_once()
        assert cost is None

    def test_landed_refresh_prices_the_next_call(self, pricing_cache):
        """An unknown model starts a background refresh; the call that
        started it reports no cost, and once it lands later calls do."""
        def _populate() -> bool:
            pricing_cache._conn.execute(
                "INSERT INTO model_pricing "
                "(model_id, prompt_cost, completion_cost, last_updated) "
                "VALUES (?, ?, ?, ?)",
                ("new-model", 0.001, 0.002, time.time()),
            )
            pricing_cache._conn.commit()
            return True

        with patch.object(pricing_cache, "refresh",
                          side_effect=_populate) as mock_refresh:
            assert pricing_cache.calculate_cost_with_refresh(
                "new-model", "", 100, 50) is None
            _settle(pricing_cache)
            cost = pricing_cache.calculate_cost_with_refresh(
                "new-model", "", 100, 50)
        mock_refresh.assert_called_once()
        assert cost == pytest.approx(100 * 0.001 + 50 * 0.002)


class TestHasInputModality:
    """Tests for the has_input_modality method."""

    def test_returns_true_for_vision_model(self, pricing_cache):
        """Should return True for a model with image input modality."""
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated, input_modalities) "
            "VALUES (?, ?, ?, ?, ?)",
            ("provider/vision-model", 0.001, 0.002, time.time(), "text,image"),
        )
        pricing_cache._conn.commit()

        assert pricing_cache.has_input_modality("vision-model", "image") is True

    def test_returns_false_for_text_only_model(self, pricing_cache):
        """Should return False for a model without image input modality."""
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated, input_modalities) "
            "VALUES (?, ?, ?, ?, ?)",
            ("provider/text-model", 0.001, 0.002, time.time(), "text"),
        )
        pricing_cache._conn.commit()

        assert pricing_cache.has_input_modality("text-model", "image") is False

    def test_returns_none_for_unknown_model(self, pricing_cache):
        """Should return None when model is not in the cache."""
        with patch.object(pricing_cache, "refresh", return_value=False):
            result = pricing_cache.has_input_modality("unknown-model", "image")
        assert result is None

    def test_case_insensitive(self, pricing_cache):
        """Should match modalities case-insensitively."""
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated, input_modalities) "
            "VALUES (?, ?, ?, ?, ?)",
            ("provider/mixed-case", 0.001, 0.002, time.time(), "Text,Image"),
        )
        pricing_cache._conn.commit()

        assert pricing_cache.has_input_modality("mixed-case", "image") is True
        assert pricing_cache.has_input_modality("mixed-case", "IMAGE") is True

    def test_default_modality_text(self, pricing_cache):
        """Should default to 'text' when input_modalities column is NULL."""
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated, input_modalities) "
            "VALUES (?, ?, ?, ?, ?)",
            ("provider/null-mod", 0.001, 0.002, time.time(), None),
        )
        pricing_cache._conn.commit()

        assert pricing_cache.has_input_modality("null-mod", "text") is True
        assert pricing_cache.has_input_modality("null-mod", "image") is False


class TestGetContextLength:
    """Tests for the get_context_length method."""

    def test_returns_context_length(self, pricing_cache):
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated, context_length) "
            "VALUES (?, ?, ?, ?, ?)",
            ("provider/test-model", 0.001, 0.002, time.time(), 128000),
        )
        pricing_cache._conn.commit()
        assert pricing_cache.get_context_length("test-model") == 128000

    def test_returns_none_for_unknown_model(self, pricing_cache):
        with patch.object(pricing_cache, "refresh", return_value=False):
            assert pricing_cache.get_context_length("unknown-model") is None

    def test_returns_none_when_context_length_not_set(self, pricing_cache):
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing "
            "(model_id, prompt_cost, completion_cost, last_updated) "
            "VALUES (?, ?, ?, ?)",
            ("provider/no-ctx", 0.001, 0.002, time.time()),
        )
        pricing_cache._conn.commit()
        assert pricing_cache.get_context_length("no-ctx") is None


def _insert_model_with_params(pricing_cache, model_id: str,
                              supported: str) -> None:
    pricing_cache._conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    pricing_cache._conn.commit()


class TestSupportsTools:
    """Tool-capability lookups for structured methods (issue #23)."""

    def test_true_when_tools_listed(self, pricing_cache):
        _insert_model_with_params(
            pricing_cache, "openai/gpt-4o", "temperature,tools,top_p")
        assert pricing_cache.supports_tools("gpt-4o") is True

    def test_false_when_tools_absent(self, pricing_cache):
        _insert_model_with_params(
            pricing_cache, "meta/plain-model", "temperature,top_p")
        assert pricing_cache.supports_tools("plain-model") is False

    def test_none_when_capability_data_empty(self, pricing_cache):
        # Another row carries capability data, so the cache is
        # post-migration: an empty column means OpenRouter reported
        # nothing for this model — no refresh, capability unknown.
        _insert_model_with_params(
            pricing_cache, "openai/gpt-4o", "temperature,tools")
        _insert_model_with_params(pricing_cache, "local/mystery-model", "")
        with patch.object(pricing_cache, "refresh") as refresh:
            assert pricing_cache.supports_tools("mystery-model") is None
        refresh.assert_not_called()

    def test_none_for_unknown_model(self, pricing_cache):
        with patch.object(pricing_cache, "refresh", return_value=False):
            assert pricing_cache.supports_tools("no-such-model") is None

    def test_pre_migration_cache_refreshes_capability_data(
            self, pricing_cache):
        """Rows cached before migration 014 have an empty
        supported_parameters column; the first capability lookup must
        refresh (in the background) so the setup gate works soon after an
        upgrade."""
        _insert_model_with_params(pricing_cache, "openai/gpt-4o", "")

        def _populate() -> bool:
            pricing_cache._conn.execute(
                "UPDATE model_pricing SET supported_parameters = ? "
                "WHERE model_id = ?",
                ("temperature,tools", "openai/gpt-4o"),
            )
            pricing_cache._conn.commit()
            return True

        with patch.object(pricing_cache, "refresh",
                          side_effect=_populate) as refresh:
            assert pricing_cache.supports_tools("gpt-4o") is None
            _settle(pricing_cache)
            assert pricing_cache.supports_tools("gpt-4o") is True
        refresh.assert_called_once()

    def test_pre_migration_cache_refresh_failure_returns_none(
            self, pricing_cache):
        _insert_model_with_params(pricing_cache, "openai/gpt-4o", "")
        with patch.object(pricing_cache, "refresh", return_value=False):
            assert pricing_cache.supports_tools("gpt-4o") is None

    def test_capability_refresh_attempted_at_most_once(self, pricing_cache):
        """A failed capability refresh (e.g. offline) must not be
        re-attempted on every call — the setup gate checks every AI
        member in a loop."""
        _insert_model_with_params(pricing_cache, "openai/gpt-4o", "")
        _insert_model_with_params(pricing_cache, "openai/gpt-4o-mini", "")
        with patch.object(pricing_cache, "refresh",
                          return_value=False) as refresh:
            assert pricing_cache.supports_tools("gpt-4o") is None
            _settle(pricing_cache)
            assert pricing_cache.supports_tools("gpt-4o-mini") is None
            _settle(pricing_cache)
        refresh.assert_called_once()


class TestUnknownModelRefreshOnce:
    """A model OpenRouter does not list (e.g. a local Ollama model) stays
    unknown after a refresh; it must not re-download the whole catalogue
    for every message priced."""

    LOOKUPS = [
        lambda c: c.calculate_cost_with_refresh("llama3", "", 10, 10),
        lambda c: c.has_input_modality("llama3", "image"),
        lambda c: c.get_context_length("llama3"),
        lambda c: c.supports_tools("llama3"),
    ]

    def test_one_refresh_across_every_lookup(self, pricing_cache):
        with patch.object(pricing_cache, "refresh",
                          return_value=False) as refresh:
            for _ in range(3):
                for lookup in self.LOOKUPS:
                    assert lookup(pricing_cache) is None
                    _settle(pricing_cache)
        refresh.assert_called_once()

    def test_each_unknown_model_gets_its_own_refresh(self, pricing_cache):
        with patch.object(pricing_cache, "refresh",
                          return_value=False) as refresh:
            pricing_cache.get_context_length("llama3")
            _settle(pricing_cache)
            pricing_cache.get_context_length("mistral")
            _settle(pricing_cache)
        assert refresh.call_count == 2
