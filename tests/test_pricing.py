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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_pricing "
        "(model_id TEXT PRIMARY KEY, prompt_cost REAL NOT NULL DEFAULT 0, "
        "completion_cost REAL NOT NULL DEFAULT 0, last_updated REAL NOT NULL DEFAULT 0)"
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
