"""Tests for the shared fire-and-forget background task helper."""

import asyncio
import logging

import pytest

from consensus.background import spawn_background


@pytest.mark.asyncio
async def test_exception_is_retrieved_and_logged(caplog):
    """A crashing background task logs its traceback rather than vanishing.

    Regression test for issue #78 defect 2: the previous done-callback never
    called ``task.exception()``, so the failure was invisible until GC.
    """
    async def boom():
        raise RuntimeError("embedding pass died")

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(boom(), "embed doc 7")
        await asyncio.gather(task, return_exceptions=True)

    assert "embed doc 7" in caplog.text
    assert "embedding pass died" in caplog.text
    assert task.exception() is not None


@pytest.mark.asyncio
async def test_successful_task_logs_nothing_and_is_released(caplog):
    """A clean task leaves no error log and drops out of the registry."""
    from consensus import background

    async def fine():
        return 42

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(fine(), "harmless")
        await task

    assert caplog.text == ""
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in background._background_tasks


@pytest.mark.asyncio
async def test_task_is_strongly_referenced_while_running():
    """The helper retains a reference so the task cannot be GC'd mid-run."""
    from consensus import background

    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.01)

    task = spawn_background(slow(), "slow one")
    await started.wait()
    assert task in background._background_tasks
    await task


@pytest.mark.asyncio
async def test_cancellation_is_not_logged_as_an_error(caplog):
    """A cancelled task is a deliberate act, not a failure to report."""
    async def forever():
        await asyncio.sleep(3600)

    with caplog.at_level(logging.ERROR, logger="consensus.background"):
        task = spawn_background(forever(), "cancelled one")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert caplog.text == ""
