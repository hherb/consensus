"""Handlers for the personal-memory and discussion-search tools."""

import logging
import uuid
from typing import Optional

from ..background import spawn_background
from ..dbkey import ScopedKey, scoped_key
from ..tools import ToolContext, ToolResult
from .constants import (
    MESSAGE_EMBED_MAX_CHARS, SEARCH_DEFAULT_LIMIT, SEARCH_SNIPPET_CHARS,
)
from .embedding import EmbeddingClient, MemoryUnavailableError, _pack_embedding, _rank_by_similarity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

# Discussions currently being indexed, so a second search does not start a
# duplicate pass. Keyed by (db_path, discussion_id), never the bare id: in
# --multi-user mode every session's first discussion is id 1, so a bare key
# let one session's in-flight pass suppress another's, and one session's
# `finally` discard clear another's marker (issue #78 whole-branch review).
_indexing_discussions: set[ScopedKey] = set()


def _tool_failure(message: str, exc: Exception) -> ToolResult:
    """Log a caught handler failure and return it as an error result (golden rule 6).

    An unreachable embedder is an expected outage (warning); anything else is
    unexpected and keeps its traceback.
    """
    if isinstance(exc, MemoryUnavailableError):
        logger.warning("%s: %s", message, exc)
    else:
        logger.error("%s: %s", message, exc, exc_info=exc)
    return ToolResult(content=f"{message}: {exc}", is_error=True)


async def _memory_store_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """memory_store: save an observation now, embed it in the background."""
    content = arguments.get("content", "").strip()
    if not content:
        return ToolResult(content="No content provided.", is_error=True)

    memory_id = str(uuid.uuid4())
    discussion_id = context.discussion_id if context.discussion_id else None

    try:
        db.add_entity_memory(
            memory_id=memory_id,
            entity_id=context.caller_entity_id,
            content=content,
            discussion_id=discussion_id,
        )
    except Exception as e:
        return _tool_failure("Failed to store memory", e)

    # Embed asynchronously in background — don't block the turn
    async def _embed_and_store():
        try:
            vec = await embed_client.embed(content)
            blob = _pack_embedding(vec)
            db.set_entity_memory_embedding(memory_id, blob)
        except MemoryUnavailableError as e:
            logger.warning("Could not embed memory %s: %s", memory_id, e)
        except Exception as e:
            logger.warning("Unexpected error embedding memory %s: %s", memory_id, e)

    spawn_background(_embed_and_store(), f"embed memory {memory_id}")

    return ToolResult(
        content=f"Memory stored (id: {memory_id}).",
        metadata={"memory_id": memory_id},
    )


async def _memory_recall_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """memory_recall: rank the caller's own memories against the query."""
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult(content="No query provided.", is_error=True)
    limit = int(arguments.get("limit", SEARCH_DEFAULT_LIMIT))

    try:
        query_vec = await embed_client.embed(query)
    except MemoryUnavailableError as e:
        return _tool_failure("Memory service unavailable", e)

    try:
        rows = db.get_entity_memories_with_embeddings(context.caller_entity_id)
    except Exception as e:
        return _tool_failure("Failed to retrieve memories", e)

    if not rows:
        return ToolResult(content="No memories stored yet.")

    top = _rank_by_similarity(query_vec, rows, limit)

    lines = [f"Recalled {len(top)} memories for '{query}':\n"]
    for i, row in enumerate(top, 1):
        lines.append(f"{i}. [{row['id']}] {row['content']}")

    return ToolResult(content="\n".join(lines), metadata={"count": len(top)})


async def _memory_forget_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """memory_forget: delete one of the caller's own memories."""
    memory_id = arguments.get("memory_id", "").strip()
    if not memory_id:
        return ToolResult(content="No memory_id provided.", is_error=True)

    try:
        deleted = db.delete_entity_memory(memory_id, context.caller_entity_id)
    except Exception as e:
        return _tool_failure("Failed to delete memory", e)

    if deleted:
        return ToolResult(content=f"Memory {memory_id} deleted.")
    return ToolResult(
        content=f"Memory {memory_id} not found or does not belong to this entity.",
        is_error=True,
    )


async def _discussion_search_handler(
    arguments: dict, context: ToolContext,
    db, embed_client: EmbeddingClient,
) -> ToolResult:
    """discussion_search: rank indexed past messages; lazily index this discussion."""
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult(content="No query provided.", is_error=True)
    limit = int(arguments.get("limit", SEARCH_DEFAULT_LIMIT))
    topic_filter: Optional[str] = arguments.get("topic_filter")

    ensure_discussion_indexed(db, embed_client, context.discussion_id)

    try:
        query_vec = await embed_client.embed(query)
    except MemoryUnavailableError as e:
        return _tool_failure("Memory service unavailable", e)

    try:
        rows = db.get_messages_with_embeddings(topic_filter)
    except Exception as e:
        return _tool_failure("Failed to search discussions", e)

    if not rows:
        return ToolResult(content="No indexed discussion messages found yet.")

    top = _rank_by_similarity(query_vec, rows, limit)

    lines = [f"Found {len(top)} relevant passages for '{query}':\n"]
    for i, row in enumerate(top, 1):
        topic = row.get("topic", "unknown topic")
        lines.append(f"{i}. [Discussion: {topic}]\n   {row['content'][:SEARCH_SNIPPET_CHARS]}")

    return ToolResult(content="\n".join(lines), metadata={"count": len(top)})


def ensure_discussion_indexed(db, embed_client: EmbeddingClient,
                              discussion_id: int) -> bool:
    """Start a background pass embedding the discussion's unindexed messages.

    The one entry point for lazy indexing (``discussion_search`` and the
    semantic context strategy both use it), so the in-flight marker is
    always added and released under the same scoped key. A failure to check
    is logged and swallowed: indexing is an optimisation, never a reason to
    fail the caller.

    Args:
        db: The database holding the discussion; scopes the marker.
        embed_client: The embedding client the pass will use.
        discussion_id: The discussion to index.

    Returns:
        True if a pass was started; False if one is already in flight,
        nothing needs indexing, or the check failed.
    """
    disc_key = scoped_key(db, discussion_id)
    if disc_key in _indexing_discussions:
        return False
    try:
        unindexed = db.get_unindexed_message_ids(discussion_id)
    except Exception as e:
        logger.warning("Could not check unindexed messages: %s", e)
        return False
    if not unindexed:
        return False
    _indexing_discussions.add(disc_key)
    spawn_background(
        _index_messages(unindexed, db, embed_client, discussion_id),
        f"index discussion {discussion_id}",
    )
    return True


async def _index_messages(
    message_ids: list[str],
    db,
    embed_client: EmbeddingClient,
    discussion_id: int,
) -> None:
    """Background task: embed and store message embeddings.

    Args:
        message_ids: Ids of the messages still lacking an embedding.
        db: The database holding them; also scopes the in-flight marker.
        embed_client: The embedding client to call per message.
        discussion_id: The discussion being indexed, for the marker and
            the log lines.
    """
    indexed = 0
    try:
        for msg_id in message_ids:
            try:
                content = db.get_message_content(msg_id)
                if not content:
                    continue
                vec = await embed_client.embed(content[:MESSAGE_EMBED_MAX_CHARS])
                blob = _pack_embedding(vec)
                db.set_message_embedding(msg_id, blob)
                indexed += 1
            except MemoryUnavailableError as e:
                # Stop if the service is down — but say so. A bare break
                # left the index silently partial, and the next search
                # reported nothing about searching half a discussion
                # (golden rule 6; issue #78 whole-branch review).
                logger.warning(
                    "Embedding service unavailable while indexing "
                    "discussion %s — stopped after %d/%d message(s): %s",
                    discussion_id, indexed, len(message_ids), e,
                )
                break
            except Exception as e:
                logger.warning("Failed to index message %s: %s", msg_id, e)
    finally:
        _indexing_discussions.discard(scoped_key(db, discussion_id))
