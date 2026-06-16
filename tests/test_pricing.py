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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_pricing "
        "(model_id TEXT PRIMARY KEY, prompt_cost REAL NOT NULL DEFAULT 0, "
        "completion_cost REAL NOT NULL DEFAULT 0, last_updated REAL NOT NULL DEFAULT 0, "
        "input_modalities TEXT DEFAULT 'text', context_length INTEGER)"
    )
    conn.commit()
    cache = PricingCache(conn, threading.Lock())
    return cache


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
        mock_refresh.assert_called_once()
        assert cost is None

    def test_refreshes_and_retries_when_model_unknown(self, pricing_cache):
        """Should refresh pricing data and retry when model initially unknown."""
        call_count = 0
        original_calculate = pricing_cache.calculate_cost

        def mock_calculate(model, url, pt, ct):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First call: unknown
            return original_calculate(model, url, pt, ct)

        # Insert pricing so second call succeeds
        pricing_cache._conn.execute(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, last_updated) "
            "VALUES (?, ?, ?, ?)",
            ("new-model", 0.001, 0.002, time.time()),
        )
        pricing_cache._conn.commit()

        with patch.object(pricing_cache, "calculate_cost", side_effect=mock_calculate):
            with patch.object(pricing_cache, "needs_refresh_for_model", return_value=True):
                with patch.object(pricing_cache, "refresh", return_value=True) as mock_refresh:
                    cost = pricing_cache.calculate_cost_with_refresh(
                        "new-model", "", 100, 50,
                    )
        assert call_count == 2
        mock_refresh.assert_called_once()
        expected_cost = 100 * 0.001 + 50 * 0.002
        assert cost is not None
        assert abs(cost - expected_cost) < 1e-10


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
