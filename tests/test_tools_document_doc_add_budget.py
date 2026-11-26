"""``doc_add`` times itself (#102).

After its fetch (``TOOL_FETCH_DEADLINE``), ``doc_add`` still parses the
document and asks the interpretation LLM for a summary (``LLM_TIMEOUT``
per call). Under the registry's generic ``TOOL_EXECUTION_TIMEOUT`` that was
cut off mid-ingestion, so it has its own cap, ``DOC_ADD_DEADLINE``. The
parse runs in a worker thread, so it no longer stalls the event loop, and
the cap can fire while it runs.

The last three tests drive the real ``ingest_document`` (#162): a cut-off
call stores nothing, and the budget fires during a slow parse or fetch.
"""

import asyncio
import threading

import httpx
import pytest

from consensus import tools
from consensus.tools import ToolContext
from consensus.tools_document import handlers, ingestion
from consensus.tools_document.provider import create_document_provider

from .document_helpers import patch_where_defined
from .image_helpers import fake_dns, install_transport


@pytest.fixture
def ctx(tmp_db, sample_ai_entity):
    """A tool context inside a real discussion."""
    discussion_id = tmp_db.create_discussion("Topic")
    return ToolContext(caller_entity_id=sample_ai_entity,
                       discussion_id=discussion_id)


def slow_ingest(seconds: float):
    """An ``ingest_document`` stand-in that takes *seconds*."""
    async def ingest(**kwargs):
        await asyncio.sleep(seconds)
        return {"document_id": 1, "title": "Slow"}
    return ingest


async def test_doc_add_is_not_cut_off_by_the_generic_tool_timeout(
        tmp_db, ctx, monkeypatch):
    monkeypatch.setattr(tools, "TOOL_EXECUTION_TIMEOUT", 0.01)
    patch_where_defined(monkeypatch, handlers._doc_add_handler,
                        "ingest_document", slow_ingest(0.05))
    registry = tools.ToolRegistry()
    registry.register_provider(create_document_provider(tmp_db))
    result = await registry.execute("doc_add", {"text": "body"},
                                    ctx.caller_entity_id, ctx.discussion_id)
    assert not result.is_error, result.content


async def test_doc_add_reports_its_own_deadline(tmp_db, ctx, monkeypatch):
    monkeypatch.setattr(handlers, "DOC_ADD_DEADLINE", 0.01)
    patch_where_defined(monkeypatch, handlers._doc_add_handler,
                        "ingest_document", slow_ingest(5))
    result = await handlers._doc_add_handler({"text": "body"}, ctx, tmp_db,
                                             None, None)
    assert result.is_error
    assert result.content == (
        "Failed to add document: it did not finish within 0.01s "
        "(fetch, parse and summary); nothing was stored")


async def test_an_inner_timeout_is_not_reported_as_the_deadline(
        tmp_db, ctx, monkeypatch):
    async def ingest(**kwargs):
        raise TimeoutError("inner")

    patch_where_defined(monkeypatch, handlers._doc_add_handler,
                        "ingest_document", ingest)
    result = await handlers._doc_add_handler({"text": "body"}, ctx, tmp_db,
                                             None, None)
    assert result.is_error
    assert result.content == "Failed to add document (TimeoutError)"


async def test_parsing_runs_off_the_event_loop(tmp_db, monkeypatch):
    seen: list[bool] = []
    real_parse = ingestion.parse_document

    def parse(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return real_parse(*args, **kwargs)

    patch_where_defined(monkeypatch, ingestion.ingest_document,
                        "parse_document", parse)
    result = await ingestion.ingest_document(
        app=None, db=tmp_db, embed_client=None, content_bytes=b"# T\n\nbody",
        filename="t.md", mime_type="text/markdown")
    assert "document_id" in result
    assert seen == [False]


# ---------------------------------------------------------------------------
# The budget against the real ingest pipeline (#162)
# ---------------------------------------------------------------------------

# A budget far below every real step, and a stall far above it.
TINY_DEADLINE = 0.05
STALL_SECONDS = 5.0
BUDGET_MESSAGE = (
    f"Failed to add document: it did not finish within {TINY_DEADLINE:g}s "
    "(fetch, parse and summary); nothing was stored")


def assert_nothing_stored(db, discussion_id: int) -> None:
    """No document row and no discussion link exist."""
    assert db.get_all_documents() == []
    assert db.get_discussion_documents(discussion_id) == []


async def test_a_slow_summary_stores_nothing(tmp_db, ctx, monkeypatch):
    """The summary call is the last await before storage; cut off there,
    the real pipeline must leave no half-stored document behind."""
    async def slow_summary(*args, **kwargs):
        await asyncio.sleep(STALL_SECONDS)
        return "never"

    monkeypatch.setattr(handlers, "DOC_ADD_DEADLINE", TINY_DEADLINE)
    patch_where_defined(monkeypatch, ingestion.ingest_document,
                        "_call_interpretation_llm", slow_summary)
    # Any truthy app enables the summary; the patched call never reads it.
    result = await handlers._doc_add_handler({"text": "# T\n\nbody"}, ctx,
                                             tmp_db, None, object())
    assert result.is_error
    assert result.content == BUDGET_MESSAGE
    assert_nothing_stored(tmp_db, ctx.discussion_id)


async def test_the_budget_fires_during_a_slow_parse(tmp_db, ctx, monkeypatch):
    """A parse blocking its thread must not block the budget."""
    release = threading.Event()
    real_parse = ingestion.parse_document

    def blocking_parse(*args, **kwargs):
        release.wait(STALL_SECONDS)  # capped, so a regression fails, not hangs
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(handlers, "DOC_ADD_DEADLINE", TINY_DEADLINE)
    patch_where_defined(monkeypatch, ingestion.ingest_document,
                        "parse_document", blocking_parse)
    result = await handlers._doc_add_handler({"text": "# T\n\nbody"}, ctx,
                                             tmp_db, None, None)
    parse_still_running = not release.is_set()
    release.set()
    assert parse_still_running
    assert result.content == BUDGET_MESSAGE
    # The abandoned parse finishes in its thread, unused.
    await asyncio.sleep(TINY_DEADLINE)
    assert_nothing_stored(tmp_db, ctx.discussion_id)


async def test_the_budget_not_the_fetch_reports_a_slow_url(
        tmp_db, ctx, monkeypatch):
    """``fetch_capped`` has its own nested deadline; the outer budget's
    cancel must still reach ``doc_add`` as the budget message, not as a
    fetch failure."""
    async def stalled(request):
        await asyncio.sleep(STALL_SECONDS)
        return httpx.Response(200, text="never")

    fake_dns(monkeypatch)
    seen = install_transport(monkeypatch, stalled)
    monkeypatch.setattr(handlers, "DOC_ADD_DEADLINE", TINY_DEADLINE)
    result = await handlers._doc_add_handler(
        {"url": "https://example.com/doc.md"}, ctx, tmp_db, None, None)
    assert len(seen) == 1
    assert result.content == BUDGET_MESSAGE
    assert_nothing_stored(tmp_db, ctx.discussion_id)
