"""Context loading strategies for participant-driven context.

Each AI participant can use a different strategy to load discussion
context from the database, rather than relying on a single in-memory
message list.

Strategies:
    full            — all messages (for short discussions)
    sliding_window  — last N messages (default, matches legacy behaviour)
    summary         — storyboard summaries for old turns + last N full messages
    semantic        — embedding-based RAG: recent messages + semantically relevant older ones
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from .models import Message, MessageRole

if TYPE_CHECKING:
    from .database import Database
    from .tools_memory import EmbeddingClient

logger = logging.getLogger(__name__)

# Default window size — matches the legacy CONTEXT_MESSAGE_LIMIT
DEFAULT_WINDOW_SIZE = 20


class ContextStrategy(str, Enum):
    """Available context loading strategies."""

    FULL = "full"
    SLIDING_WINDOW = "sliding_window"
    SUMMARY = "summary"
    SEMANTIC = "semantic"
    TOKEN_WINDOW = "token_window"


@dataclass
class ContextConfig:
    """Per-participant context loading configuration."""

    strategy: ContextStrategy = ContextStrategy.SLIDING_WINDOW
    window_size: int = DEFAULT_WINDOW_SIZE
    # Token-aware windowing fields (populated by Moderator)
    model_context_length: Optional[int] = None
    reserved_output_tokens: int = 0
    system_tokens: int = 0

    @classmethod
    def from_member_row(cls, member: dict, discussion: dict) -> ContextConfig:
        """Build from DB rows.  Member columns override discussion defaults.

        Uses explicit ``is not None`` checks so that intentional zero/empty
        values are not silently replaced by fallback defaults.
        """
        m_strat = member.get("context_strategy")
        d_strat = discussion.get("default_context_strategy")
        strategy_str = (
            m_strat if m_strat is not None
            else d_strat if d_strat is not None
            else "sliding_window"
        )

        m_win = member.get("context_window_size")
        d_win = discussion.get("default_context_window_size")
        window = (
            m_win if m_win is not None
            else d_win if d_win is not None
            else DEFAULT_WINDOW_SIZE
        )

        try:
            strategy = ContextStrategy(strategy_str)
        except ValueError:
            logger.warning("Unknown context strategy %r, falling back to sliding_window",
                           strategy_str)
            strategy = ContextStrategy.SLIDING_WINDOW
        return cls(strategy=strategy, window_size=int(window))


def load_context_messages(
    db: Database,
    discussion_id: int,
    config: ContextConfig,
) -> list[Message]:
    """Load discussion messages from DB according to the given strategy.

    Returns a chronologically ordered list of ``Message`` objects ready
    for the moderator's context-formatting pipeline.
    """
    if config.strategy == ContextStrategy.FULL:
        return _load_full(db, discussion_id)
    if config.strategy == ContextStrategy.SUMMARY:
        return _load_summary(db, discussion_id, config.window_size)
    if config.strategy == ContextStrategy.TOKEN_WINDOW:
        return _load_token_window(db, discussion_id, config)
    # Default: sliding_window (SEMANTIC is handled by load_context_messages_async)
    return _load_sliding_window(db, discussion_id, config.window_size)


def _load_full(db: Database, discussion_id: int) -> list[Message]:
    """Return every message in the discussion."""
    rows = db.get_messages(discussion_id)
    return [Message.from_db_row(r) for r in rows]


def _load_sliding_window(
    db: Database, discussion_id: int, window_size: int,
) -> list[Message]:
    """Return the last *window_size* messages."""
    rows = db.get_messages_windowed(discussion_id, window_size)
    return [Message.from_db_row(r) for r in rows]


# Safety margin for token estimation inaccuracy
_TOKEN_SAFETY_MARGIN = 200


def _load_token_window(
    db: Database, discussion_id: int, config: ContextConfig,
) -> list[Message]:
    """Fill context window based on token budget rather than message count.

    Falls back to sliding_window if the model's context length is unknown.
    """
    from .token_utils import estimate_tokens

    if not config.model_context_length:
        logger.info("No context_length for model; falling back to sliding_window")
        return _load_sliding_window(db, discussion_id, config.window_size)

    available = (config.model_context_length
                 - config.reserved_output_tokens
                 - config.system_tokens
                 - _TOKEN_SAFETY_MARGIN)

    if available <= 0:
        logger.warning("No token budget remaining for context messages")
        return []

    # Load all messages (v1 simplicity; batched loading is a future optimisation)
    all_rows = db.get_messages(discussion_id)
    if not all_rows:
        return []

    # Walk backwards, accumulating tokens until budget is exhausted
    selected: list[dict] = []
    tokens_used = 0
    for row in reversed(all_rows):
        content = row.get("content", "")
        entity_name = row.get("entity_name", "")
        # Estimate as it will appear in the formatted message
        msg_text = f"[{entity_name}]: {content}"
        msg_tokens = estimate_tokens(msg_text) + 4  # per-message overhead
        if tokens_used + msg_tokens > available:
            break
        selected.append(row)
        tokens_used += msg_tokens

    selected.reverse()  # back to chronological order
    logger.debug("Token window: used ~%d tokens of %d budget, selected %d messages",
                 tokens_used, available, len(selected))
    return [Message.from_db_row(r) for r in selected]


def _load_summary(
    db: Database, discussion_id: int, window_size: int,
) -> list[Message]:
    """Storyboard summaries for older turns, full messages for recent ones.

    The most recent *window_size* messages are returned verbatim.
    For everything before them, storyboard summaries are used as
    synthetic system messages that give the AI a compressed view
    of earlier discussion.  If no storyboard entries exist for the
    older portion, falls back to the sliding-window strategy.
    """
    total = db.get_messages_count(discussion_id)
    if total <= window_size:
        # Discussion fits within the window — just return everything.
        return _load_full(db, discussion_id)

    # Recent messages at full fidelity
    recent_rows = db.get_messages_windowed(discussion_id, window_size)
    recent_msgs = [Message.from_db_row(r) for r in recent_rows]

    # Storyboard summaries for older turns
    storyboard = db.get_storyboard(discussion_id)
    if not storyboard:
        # No summaries available — fall back to sliding window
        return recent_msgs

    # Find the timestamp boundary: anything before the first recent
    # message is "old" and represented by storyboard summaries.
    boundary_ts = recent_msgs[0].timestamp if recent_msgs else 0.0
    old_summaries = [s for s in storyboard if s["timestamp"] < boundary_ts]

    if not old_summaries:
        return recent_msgs

    # Build synthetic messages from storyboard entries
    summary_msgs: list[Message] = []
    for entry in old_summaries:
        speaker = entry.get("speaker_name") or "Moderator"
        summary_msgs.append(Message(
            entity_id=entry.get("speaker_entity_id") or 0,
            entity_name=speaker,
            content=f"[Summary of earlier discussion — turn {entry['turn_number']}]: {entry['summary']}",
            role=MessageRole.MODERATOR,
            timestamp=entry["timestamp"],
            id=0,
        ))

    return summary_msgs + recent_msgs


# ---------------------------------------------------------------------------
# Async context loading (required for semantic strategy)
# ---------------------------------------------------------------------------

async def load_context_messages_async(
    db: Database,
    discussion_id: int,
    config: ContextConfig,
    embed_client: Optional[EmbeddingClient] = None,
) -> list[Message]:
    """Async variant of :func:`load_context_messages`.

    Required for the ``semantic`` strategy which needs an async embedding
    call.  For all other strategies this simply delegates to the sync
    version.
    """
    if config.strategy == ContextStrategy.SEMANTIC:
        if embed_client is None:
            logger.warning(
                "Semantic strategy requested but no embed_client provided; "
                "falling back to sliding_window")
            return _load_sliding_window(db, discussion_id, config.window_size)
        return await _load_semantic(
            db, discussion_id, config.window_size, embed_client)
    return load_context_messages(db, discussion_id, config)


async def _load_semantic(
    db: Database,
    discussion_id: int,
    window_size: int,
    embed_client: EmbeddingClient,
) -> list[Message]:
    """Hybrid semantic + recency context loading.

    Always includes the most recent messages for immediate conversational
    context, then fills the remaining budget with the semantically most
    relevant older messages from the same discussion.
    """
    # Local imports to avoid circular dependencies
    from .tools_memory import (
        _rank_by_similarity,
        _index_messages, _indexing_discussions,
        MemoryUnavailableError,
    )

    # -- 1. Budget split ------------------------------------------------
    recency_count = max(3, window_size // 4)
    semantic_budget = window_size - recency_count

    # -- 2. Recent messages (always included) ---------------------------
    recent_rows = db.get_messages_windowed(discussion_id, recency_count)
    recent_msgs = [Message.from_db_row(r) for r in recent_rows]
    recent_ids = {m.id for m in recent_msgs}

    if semantic_budget <= 0:
        return recent_msgs

    # -- 3. Trigger lazy indexing (non-blocking) ------------------------
    if discussion_id not in _indexing_discussions:
        try:
            unindexed = db.get_unindexed_message_ids(discussion_id)
            if unindexed:
                _indexing_discussions.add(discussion_id)
                asyncio.create_task(
                    _index_messages(unindexed, db, embed_client, discussion_id))
        except Exception as exc:
            logger.warning("Could not trigger lazy indexing: %s", exc)

    # -- 4. Build query text from topic + recent messages ---------------
    query_parts: list[str] = []
    try:
        disc_row = db.get_discussion(discussion_id)
        if disc_row:
            topic = disc_row.get("topic", "")
            if topic:
                query_parts.append(topic)
    except Exception as exc:
        logger.debug("Could not fetch discussion topic: %s", exc)
    for m in recent_msgs[-3:]:
        query_parts.append(m.content[:200])
    query_text = "\n".join(query_parts)

    if not query_text.strip():
        return _load_sliding_window(db, discussion_id, window_size)

    # -- 5. Embed the query ---------------------------------------------
    try:
        query_vec = await embed_client.embed(query_text)
    except MemoryUnavailableError:
        logger.warning(
            "Embedding service unavailable; falling back to sliding_window")
        return _load_sliding_window(db, discussion_id, window_size)

    # -- 6. Retrieve embedded messages for this discussion --------------
    all_embedded = db.get_messages_with_embeddings_for_discussion(discussion_id)
    if not all_embedded:
        logger.info(
            "No embeddings available yet for discussion %d; "
            "falling back to sliding_window", discussion_id)
        return _load_sliding_window(db, discussion_id, window_size)

    # -- 7. Filter out recent messages and rank by similarity -----------
    candidates = [r for r in all_embedded if r["id"] not in recent_ids]
    if not candidates:
        return recent_msgs

    top_semantic = _rank_by_similarity(query_vec, candidates, semantic_budget)

    # -- 8. Convert to Message objects ----------------------------------
    semantic_msgs: list[Message] = []
    for r in top_semantic:
        try:
            role = MessageRole(r["role"]) if r.get("role") else MessageRole.PARTICIPANT
        except ValueError:
            role = MessageRole.PARTICIPANT
        semantic_msgs.append(Message(
            id=r["id"],
            entity_id=r["entity_id"],
            entity_name=r["entity_name"],
            content=r["content"],
            role=role,
            timestamp=r["timestamp"],
        ))

    # -- 9. Merge and sort chronologically ------------------------------
    all_msgs = semantic_msgs + recent_msgs
    all_msgs.sort(key=lambda m: m.timestamp)
    return all_msgs
