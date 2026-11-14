"""The tool-capability gate waits for an in-flight catalogue download
(issue #118).

Since #115 the OpenRouter download runs in a background thread and
``supports_tools()`` answers ``None`` until it lands, which the gate lets
through. On a fresh DB (first desktop launch, every new ``--multi-user``
session) the startup download is in flight when the user starts, so a
structured method with a model OpenRouter reports as lacking ``tools``
passed setup and failed at runtime. Setup and the method recommender now
wait, bounded and off the event loop, for that download first.
"""

import asyncio
import threading
import time

import pytest

from consensus import pricing as pricing_module
from consensus.app import ConsensusApp
from consensus.app_discussion_setup import recommend_method
from consensus.methods.recommender import MethodRecommendation
from consensus.pricing import PricingCache

#: Generous upper bound so a broken wait fails instead of hanging.
BLOCK_SECONDS = 5.0


def _insert_model(cache: PricingCache, model_id: str,
                  supported: str) -> None:
    """Store one catalogue row, as a landed download would."""
    with cache._lock:
        cache._conn.execute(
            "INSERT OR REPLACE INTO model_pricing (model_id, prompt_cost,"
            " completion_cost, last_updated, input_modalities,"
            " context_length, supported_parameters)"
            " VALUES (?, 0, 0, ?, 'text', 8192, ?)",
            (model_id, time.time(), supported))
        cache._conn.commit()


def close_app(app: ConsensusApp, release: threading.Event) -> None:
    """Let any download finish, then close the DB: closing it under a
    thread still writing to it crashes the interpreter."""
    release.set()
    thread = app.db.pricing._refresh_thread
    if thread is not None:
        thread.join(BLOCK_SECONDS * 2)
        assert not thread.is_alive()
    app.db.close()


@pytest.fixture
def slow_download(monkeypatch):
    """Make every app's startup download land after a short delay with a
    catalogue in which ``llama3`` lacks tool support."""
    release = threading.Event()

    def refresh(self) -> bool:
        release.wait(BLOCK_SECONDS)
        _insert_model(self, "meta/llama3", "temperature,top_p")
        return True

    monkeypatch.setattr(PricingCache, "refresh", refresh)
    yield release
    release.set()


@pytest.fixture
def fresh_app(tmp_path, slow_download):
    """A ConsensusApp on a fresh DB with its startup download in flight,
    set up for a structured method with an AI participant on ``llama3``."""
    app = ConsensusApp(db_path=str(tmp_path / "fresh.db"))
    pid = app.db.add_provider("Local", "http://localhost:11434/v1", "")
    mod = app.db.add_entity("Mod", "human", "#aaa")
    ai = app.db.add_entity("Alice", "ai", "#bbb", pid, "llama3", 0.7,
                           1024, "")
    human = app.db.add_entity("Bob", "human", "#ccc")
    app.add_to_discussion(mod, is_moderator=True)
    app.add_to_discussion(ai)
    app.add_to_discussion(human)
    app.set_topic("Pick a database")
    app.set_discussion_method("delphi")
    assert app.db.pricing._refresh_thread.is_alive()
    yield app
    close_app(app, slow_download)


async def test_start_waits_for_download_then_blocks(fresh_app, slow_download):
    """The gate sees the landed catalogue, not the empty pre-download one."""
    asyncio.get_running_loop().call_later(0.2, slow_download.set)
    result = await fresh_app.start_discussion()
    assert "error" in result, result
    assert "llama3" in result["error"]
    assert "tool" in result["error"].lower()


async def test_wait_does_not_block_the_event_loop(fresh_app, slow_download):
    """Other requests keep being served while setup waits."""
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while not slow_download.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    asyncio.get_running_loop().call_later(0.2, slow_download.set)
    await asyncio.gather(fresh_app.start_discussion(), ticker())
    assert ticks > 5


async def test_wait_is_bounded(fresh_app, slow_download, monkeypatch, caplog):
    """A download stuck in its retries cannot hold setup for long: the gate
    falls back to the data it has, and says so in the log."""
    monkeypatch.setattr(pricing_module, "CAPABILITY_WAIT_SECONDS", 0.1)
    started = time.monotonic()
    result = await fresh_app.start_discussion()
    assert time.monotonic() - started < BLOCK_SECONDS / 2
    assert "error" not in result, result
    assert "still downloading" in caplog.text


async def test_recommender_waits_for_download(tmp_path, slow_download):
    """Down-ranking sees the landed catalogue too."""
    app = ConsensusApp(db_path=str(tmp_path / "rec.db"))

    class Recommender:
        async def recommend(self, **kwargs):
            return [MethodRecommendation(
                method_name="delphi", display_name="Delphi",
                confidence=0.9, reasoning="fits")]

    asyncio.get_running_loop().call_later(0.2, slow_download.set)
    try:
        import consensus.methods.recommender as rec_module
        real = rec_module.MethodRecommender
        rec_module.MethodRecommender = Recommender
        try:
            recs = await recommend_method(
                "topic", "", ai_client=None, provider={}, db=app.db,
                panel_models=[("llama3", "http://localhost:11434/v1")])
        finally:
            rec_module.MethodRecommender = real
    finally:
        close_app(app, slow_download)
    assert recs[0]["capability_warning"], recs


async def test_no_download_in_flight_returns_at_once(tmp_path):
    """A cache with nothing downloading does not wait at all."""
    app = ConsensusApp(db_path=str(tmp_path / "idle.db"))
    try:
        app.db.pricing._refresh_thread.join(BLOCK_SECONDS)
        started = time.monotonic()
        assert await app.db.pricing.wait_for_refresh() is True
        assert time.monotonic() - started < 0.5
    finally:
        close_app(app, threading.Event())


def test_startup_refreshes_a_cache_without_capability_data(
        tmp_path, monkeypatch):
    """A fresh-but-pre-migration-014 cache starts its download at app
    start, so the gate has something to wait for (it used to start only
    from the first ``supports_tools()`` lookup, inside the gate itself)."""
    db_path = str(tmp_path / "old.db")
    app = ConsensusApp(db_path=db_path)
    app.db.pricing._refresh_thread.join(BLOCK_SECONDS)
    _insert_model(app.db.pricing, "meta/llama3", "")  # no capability data
    app.db.close()

    started: list[str] = []
    monkeypatch.setattr(PricingCache, "refresh",
                        lambda self: started.append("x") or False)
    app = ConsensusApp(db_path=db_path)
    try:
        app.db.pricing._refresh_thread.join(BLOCK_SECONDS)
        assert started == ["x"]
    finally:
        close_app(app, threading.Event())
