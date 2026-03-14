"""Context loading strategies for participant-driven context.

Each AI participant can use a different strategy to load discussion
context from the database, rather than relying on a single in-memory
message list.

Strategies:
    full            — all messages (for short discussions)
    sliding_window  — last N messages (default, matches legacy behaviour)
    summary         — storyboard summaries for old turns + last N full messages
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .models import Message, MessageRole

if TYPE_CHECKING:
    from .database import Database

logger = logging.getLogger(__name__)

# Default window size — matches the legacy CONTEXT_MESSAGE_LIMIT
DEFAULT_WINDOW_SIZE = 20


class ContextStrategy(str, Enum):
    """Available context loading strategies."""

    FULL = "full"
    SLIDING_WINDOW = "sliding_window"
    SUMMARY = "summary"


@dataclass
class ContextConfig:
    """Per-participant context loading configuration."""

    strategy: ContextStrategy = ContextStrategy.SLIDING_WINDOW
    window_size: int = DEFAULT_WINDOW_SIZE

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
    # Default: sliding_window
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
