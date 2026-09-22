"""Fire-and-forget asyncio task scheduling with visible failures.

``asyncio`` holds only a weak reference to a task, so an un-retained
background task can be garbage-collected mid-run; and a task whose
exception is never retrieved reports its failure only as asyncio's generic
"Task exception was never retrieved" warning at GC time — which is to say,
usually not at all.  Both traps are easy to reproduce and were present in
two copies of the same helper (issue #78 defect 2), so the fix lives here
once (golden rule 1).
"""

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks, discarded when each completes.
_background_tasks: set[asyncio.Task] = set()


def _log_task_outcome(task: asyncio.Task, description: str) -> None:
    """Retrieve a finished task's exception and log it.

    Retrieval is the point: leaving the exception unretrieved is what makes
    the failure invisible.  Cancellation is deliberate and is not reported
    as an error (golden rule 6 is about *caught errors*, not about noise).
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task failed (%s): %s", description, exc,
            exc_info=exc,
        )


def spawn_background(
    coro: Coroutine[Any, Any, Any], description: str,
) -> asyncio.Task:
    """Schedule *coro* as a background task whose failure will be logged.

    Args:
        coro: The coroutine to run detached from the caller.
        description: Short human-readable label naming the work, used in
            the log line when the task fails (e.g. ``"embed doc 7"``).

    Returns:
        The scheduled task.  Callers may ignore it; the helper keeps its
        own strong reference until it completes.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(lambda t: _log_task_outcome(t, description))
    return task
