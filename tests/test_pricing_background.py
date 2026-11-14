"""PricingCache never downloads on the caller's thread (issue #115).

Every lookup is reached from the event loop (web handlers, the desktop
bridge's loop, ``SessionManager`` building a ``ConsensusApp`` per visitor),
so a download there stalls every request for as long as the network takes.
"""

import asyncio
import sqlite3
import threading

import httpx
import pytest

from consensus.pricing import OPENROUTER_MODELS_URL, PricingCache
from tests.image_helpers import install_transport

#: The real method, captured before conftest's autouse stub replaces it.
REAL_REFRESH = PricingCache.refresh

#: Long enough that a lookup waiting on the refresh would visibly hang.
BLOCK_SECONDS = 5.0

CATALOGUE = {"data": [{
    "id": "openai/gpt-4o",
    "pricing": {"prompt": "0.001", "completion": "0.002"},
    "architecture": {"input_modalities": ["text", "image"]},
    "context_length": 128000,
    "supported_parameters": ["tools", "temperature"],
}]}


@pytest.fixture
def cache(tmp_path):
    """A PricingCache on the production schema, shareable across threads
    as ``Database``'s connection is, with the real ``refresh`` (each test
    fakes the wire)."""
    conn = sqlite3.connect(str(tmp_path / "p.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE model_pricing (model_id TEXT PRIMARY KEY, "
        "prompt_cost REAL NOT NULL DEFAULT 0, "
        "completion_cost REAL NOT NULL DEFAULT 0, "
        "last_updated REAL NOT NULL DEFAULT 0, "
        "input_modalities TEXT DEFAULT 'text', context_length INTEGER, "
        "supported_parameters TEXT DEFAULT '')")
    conn.commit()
    pricing = PricingCache(conn, threading.Lock())
    pricing.refresh = REAL_REFRESH.__get__(pricing)
    yield pricing
    conn.close()


@pytest.fixture
def blocking_refresh(cache, monkeypatch):
    """Replace ``refresh`` with one that blocks until released.

    Yields ``(release, threads)``: set ``release`` to let it finish;
    ``threads`` names the thread of every call.
    """
    release = threading.Event()
    threads: list[threading.Thread] = []

    def refresh() -> bool:
        threads.append(threading.current_thread())
        release.wait(BLOCK_SECONDS)
        return False

    monkeypatch.setattr(cache, "refresh", refresh)
    yield release, threads
    release.set()


LOOKUPS = [
    lambda c: c.calculate_cost_with_refresh("new-model", "", 10, 10),
    lambda c: c.has_input_modality("new-model", "image"),
    lambda c: c.get_context_length("new-model"),
    lambda c: c.supports_tools("new-model"),
]


@pytest.mark.parametrize("lookup", LOOKUPS)
def test_unknown_model_lookup_does_not_wait_for_download(
        cache, blocking_refresh, lookup):
    release, threads = blocking_refresh
    assert lookup(cache) is None  # returns while the download is held
    assert cache._refresh_thread.is_alive()
    release.set()
    cache._refresh_thread.join()
    assert threads and threads[0] is not threading.main_thread()


def test_capability_refresh_does_not_wait_for_download(
        cache, blocking_refresh):
    """A pre-migration-014 cache (no capability data) refreshes off-thread."""
    cache._conn.execute(
        "INSERT INTO model_pricing (model_id) VALUES ('openai/gpt-4o')")
    cache._conn.commit()
    release, threads = blocking_refresh
    assert cache.supports_tools("gpt-4o") is None
    assert cache._refresh_thread.is_alive()
    release.set()
    cache._refresh_thread.join()
    assert len(threads) == 1 and threads[0] is not threading.main_thread()


def test_one_download_in_flight(cache, blocking_refresh):
    """A model asked for mid-download is covered by that download (it
    fetches the whole catalogue), so no second one starts."""
    release, threads = blocking_refresh
    cache.get_context_length("model-a")
    cache.get_context_length("model-b")
    release.set()
    cache._refresh_thread.join()
    assert len(threads) == 1


def test_landed_refresh_answers_next_lookup(cache, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(
        200, json=CATALOGUE))
    assert cache.get_context_length("gpt-4o") is None
    cache._refresh_thread.join()
    assert cache.get_context_length("gpt-4o") == 128000
    assert cache.supports_tools("gpt-4o") is True
    assert cache.calculate_cost("gpt-4o", "", 1000, 1000) == pytest.approx(3.0)


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip ``fetch_capped``'s backoff delays."""
    async def fake_sleep(seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def test_refresh_retries_a_transient_failure(cache, monkeypatch, no_sleep):
    """Golden rule 5: one 503 must not leave the model unpriced for the
    whole process."""
    replies = iter([httpx.Response(503), httpx.Response(200, json=CATALOGUE)])
    seen = install_transport(monkeypatch, lambda request: next(replies))
    assert cache.refresh() is True
    assert [str(r.url) for r in seen] == [OPENROUTER_MODELS_URL] * 2
    assert cache.get_context_length("gpt-4o") == 128000


@pytest.mark.parametrize("reply", [
    httpx.Response(404),
    httpx.Response(200, content=b"<html>gateway</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"data": []}),
])
def test_refresh_failure_returns_false(cache, monkeypatch, no_sleep, reply):
    install_transport(monkeypatch, lambda request: reply)
    assert cache.refresh() is False


def test_refresh_after_db_closed_returns_false(cache, monkeypatch):
    """A session can expire (closing its DB) mid-download."""
    install_transport(monkeypatch, lambda request: httpx.Response(
        200, json=CATALOGUE))
    cache._conn.close()
    assert cache.refresh() is False


def test_app_start_with_stale_cache_does_not_wait(tmp_path, monkeypatch):
    """``SessionManager`` builds a ``ConsensusApp`` inside a request
    handler for every new ``--multi-user`` visitor, each on an empty DB."""
    from consensus.app import ConsensusApp
    release = threading.Event()
    threads: list[threading.Thread] = []

    def refresh(self) -> bool:
        threads.append(threading.current_thread())
        release.wait(BLOCK_SECONDS)
        return False

    monkeypatch.setattr(PricingCache, "refresh", refresh)
    app = ConsensusApp(db_path=str(tmp_path / "app.db"))
    try:
        assert app.db.pricing._refresh_thread.is_alive()
        release.set()
        app.db.pricing._refresh_thread.join()
        assert len(threads) == 1
        assert threads[0] is not threading.main_thread()
    finally:
        release.set()
        app.db.close()

