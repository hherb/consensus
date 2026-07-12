"""Discussion state management — pause, resume, reopen, reset, load, export, delete, restore."""

import json
import time
from typing import Callable, Optional

from .database import Database
from .models import Discussion, Entity, Message, MessageRole, StoryboardEntry
from .moderator import Moderator


def get_export_data(db: Database, discussion_id: int) -> dict:
    """Get discussion data for export without mutating current state.

    Args:
        db: Database instance.
        discussion_id: ID of the discussion to export.

    Returns:
        Serialised discussion dict, or ``{"error": ...}`` on failure.
    """
    disc = db.get_discussion(discussion_id)
    if not disc:
        return {"error": "Discussion not found"}

    members = db.get_discussion_members(discussion_id)
    messages = db.get_messages(discussion_id)
    storyboard = db.get_storyboard(discussion_id)

    entities = [Entity.from_db_row(m) for m in members]
    msgs = [Message.from_db_row(m) for m in messages]
    sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

    turn_order = [
        m["entity_id"] for m in members
        if m.get("turn_position") is not None
    ]

    status = disc["status"]
    d = Discussion(
        id=discussion_id,
        topic=disc["topic"],
        entities=entities,
        moderator_id=disc.get("moderator_id"),
        messages=msgs,
        storyboard=sb,
        turn_order=turn_order,
        is_active=status == "active",
        status=status,
    )
    return d.to_dict()


def load_discussion(
    db: Database,
    discussion_id: int,
    key_resolver: Callable,
    tool_registry: Optional[object],
) -> "tuple[Discussion, Moderator] | dict":
    """Load a past discussion, restoring full state including turn position.

    Args:
        db: Database instance.
        discussion_id: ID of the discussion to load.
        key_resolver: Callable used by :class:`Moderator` to resolve API keys.
        tool_registry: Tool registry passed to the new Moderator.

    Returns:
        ``(Discussion, Moderator)`` on success, or ``{"error": ...}`` dict.
    """
    disc = db.get_discussion(discussion_id)
    if not disc:
        return {"error": "Discussion not found"}

    members = db.get_discussion_members(discussion_id)
    messages = db.get_messages(discussion_id)
    storyboard = db.get_storyboard(discussion_id)

    entities = [Entity.from_db_row(m) for m in members]
    msgs = [Message.from_db_row(m) for m in messages]
    sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

    # Restore turn order from discussion_members.turn_position
    turn_order: list[int] = [
        m["entity_id"] for m in members
        if m.get("turn_position") is not None
    ]

    status = disc["status"]
    is_active = status == "active"

    # Restore method state (needed below for the phase turn order)
    discussion_method = disc.get("discussion_method", "open_discussion")
    method_state_raw = disc.get("method_state", "{}")
    try:
        method_state = json.loads(method_state_raw) if method_state_raw else {}
    except (json.JSONDecodeError, TypeError):
        method_state = {}

    # The members table holds the full setup roster; the current phase
    # may run a narrowed order recorded in method_state (issue #16).
    base_turn_order = list(turn_order)
    saved_order = method_state.get("_turn_order")
    member_ids = {m["entity_id"] for m in members}
    if (isinstance(saved_order, list) and saved_order
            and all(eid in member_ids for eid in saved_order)):
        turn_order = list(saved_order)

    # Recover turn state for resumable discussions
    current_turn_index = 0
    turn_number = 0
    if status in ("active", "paused") and turn_order and msgs:
        # Live invariant after advance_turn: turn_number is one past the
        # last recorded turn (matches reopen/continue restore paths).
        turn_number = db.get_max_turn_number(discussion_id) + 1
        # Find the last participant message to determine next speaker
        last_participant = next(
            (m for m in reversed(msgs)
             if m.role == MessageRole.PARTICIPANT),
            None,
        )
        if last_participant and last_participant.entity_id in turn_order:
            last_idx = turn_order.index(last_participant.entity_id)
            current_turn_index = (last_idx + 1) % len(turn_order)
        turn_number = max(turn_number, 1)

    # Restore member roles from DB
    member_roles = {
        m["entity_id"]: m.get("participant_role", "standard")
        for m in members
    }

    discussion = Discussion(
        id=discussion_id,
        topic=disc["topic"],
        entities=entities,
        moderator_id=disc.get("moderator_id"),
        messages=msgs,
        storyboard=sb,
        turn_order=turn_order,
        base_turn_order=base_turn_order,
        current_turn_index=current_turn_index,
        turn_number=turn_number,
        max_rounds=disc.get("max_rounds", 0),
        is_active=is_active,
        status=status,
        member_roles=member_roles,
        discussion_method=discussion_method,
        method_state=method_state,
        cost_limit=disc.get("cost_limit", 0.0),
        default_context_strategy=disc.get("default_context_strategy", "sliding_window"),
        default_context_window_size=disc.get("default_context_window_size", 20),
    )
    moderator = Moderator(
        discussion, db,
        key_resolver=key_resolver,
        tool_registry=tool_registry,
    )
    return (discussion, moderator)


def delete_discussions(db: Database, discussion_ids: list[int]) -> dict:
    """Soft-delete discussions by IDs.

    Args:
        db: Database instance.
        discussion_ids: List of discussion IDs to soft-delete.

    Returns:
        ``{"deleted": count}`` dict.
    """
    count = db.soft_delete_discussions(discussion_ids)
    return {"deleted": count}


def restore_discussion(db: Database, discussion_id: int) -> dict:
    """Restore a soft-deleted discussion.

    Args:
        db: Database instance.
        discussion_id: ID of the discussion to restore.

    Returns:
        ``{"restored": bool}`` dict.
    """
    restored = db.restore_discussion(discussion_id)
    return {"restored": restored}


def pause_discussion(discussion: Discussion, db: Database) -> dict:
    """Pause the current active discussion.

    Mutates the discussion in-place: sets status to ``"paused"`` and appends
    a system message.

    Args:
        discussion: The active discussion to pause.
        db: Database instance.

    Returns:
        ``discussion.to_dict()`` on success, or ``{"error": ...}`` dict.
    """
    if not discussion.id or discussion.status != "active":
        return {"error": "Discussion is not active"}

    discussion.status = "paused"
    discussion.is_active = False
    db.update_discussion(discussion.id, status="paused")

    mod_id = discussion.moderator_id or 0
    sys_msg = Message(
        entity_id=mod_id, entity_name="System",
        content="-- Discussion paused --",
        role=MessageRole.SYSTEM,
    )
    discussion.messages.append(sys_msg)
    db.add_message(
        discussion.id, mod_id,
        "-- Discussion paused --", "system",
        turn_number=discussion.turn_number,
    )
    return discussion.to_dict()


def _increase_budgets(discussion: Discussion, db: Database) -> None:
    """Increase round and cost budgets for a discussion being continued.

    Tracks continuation count in ``method_state`` and scales both
    ``max_rounds`` and ``cost_limit`` proportionally so that accumulated
    progress doesn't immediately trigger the limit on resumption.
    """
    continuation_count = discussion.method_state.get("_continuation_count", 1)
    continuation_count += 1
    discussion.method_state["_continuation_count"] = continuation_count

    updates: dict = {}

    if discussion.max_rounds > 0:
        original_budget = discussion.method_state.get("_original_max_rounds")
        if original_budget is None:
            original_budget = discussion.max_rounds
            discussion.method_state["_original_max_rounds"] = original_budget
        if original_budget > 0:
            discussion.max_rounds = original_budget * continuation_count
            updates["max_rounds"] = discussion.max_rounds

    if discussion.cost_limit > 0:
        original_cost_limit = discussion.method_state.get("_original_cost_limit")
        if original_cost_limit is None:
            original_cost_limit = discussion.cost_limit
            discussion.method_state["_original_cost_limit"] = original_cost_limit
        if original_cost_limit > 0:
            discussion.cost_limit = original_cost_limit * continuation_count
            updates["cost_limit"] = discussion.cost_limit

    updates["method_state"] = json.dumps(discussion.method_state)
    db.update_discussion(discussion.id, **updates)


def resume_discussion(discussion: Discussion, db: Database) -> dict:
    """Resume a paused discussion.

    Mutates the discussion in-place: sets status to ``"active"`` and appends
    a system message.  If rounds or cost limits have been exhausted,
    increases the budgets so the discussion can continue.

    Args:
        discussion: The paused discussion to resume.
        db: Database instance.

    Returns:
        ``discussion.to_dict()`` on success, or ``{"error": ...}`` dict.
    """
    if not discussion.id or discussion.status != "paused":
        return {"error": "Discussion is not paused"}

    # If rounds or cost are already at the limit, increase the budgets
    rounds_exhausted = (
        discussion.max_rounds > 0
        and discussion.current_round > discussion.max_rounds
    )
    cost_exhausted = False
    if discussion.cost_limit > 0:
        from .app_discussion_flow import calculate_discussion_cost
        cost_exhausted = calculate_discussion_cost(discussion) >= discussion.cost_limit
    if rounds_exhausted or cost_exhausted:
        _increase_budgets(discussion, db)

    discussion.status = "active"
    discussion.is_active = True
    db.update_discussion(discussion.id, status="active")

    mod_id = discussion.moderator_id or 0
    sys_msg = Message(
        entity_id=mod_id, entity_name="System",
        content="-- Discussion resumed --",
        role=MessageRole.SYSTEM,
    )
    discussion.messages.append(sys_msg)
    db.add_message(
        discussion.id, mod_id,
        "-- Discussion resumed --", "system",
        turn_number=discussion.turn_number,
    )
    return discussion.to_dict()


def reopen_discussion(discussion: Discussion, db: Database) -> dict:
    """Reopen a concluded discussion for continuation.

    Transitions the discussion to ``"paused"`` so the user can manage
    participants before resuming with a new prompt.

    Args:
        discussion: The concluded discussion to reopen.
        db: Database instance.

    Returns:
        ``discussion.to_dict()`` on success, or ``{"error": ...}`` dict.
    """
    if not discussion.id:
        return {"error": "No discussion loaded"}
    if discussion.status != "concluded":
        return {"error": "Discussion is not concluded"}

    # Increase budgets so exhausted rounds/cost don't immediately re-trigger
    _increase_budgets(discussion, db)

    discussion.status = "paused"
    discussion.is_active = False
    db.update_discussion(
        discussion.id, status="paused", ended_at=None,
    )

    # Restore turn state so the discussion can continue
    if discussion.turn_order:
        discussion.current_turn_index = 0
    discussion.turn_number = (
        db.get_max_turn_number(discussion.id) + 1
    )

    mod_id = discussion.moderator_id or 0
    sys_msg = Message(
        entity_id=mod_id, entity_name="System",
        content="-- Discussion reopened --",
        role=MessageRole.SYSTEM,
    )
    discussion.messages.append(sys_msg)
    db.add_message(
        discussion.id, mod_id,
        "-- Discussion reopened --", "system",
        turn_number=discussion.turn_number,
    )
    return discussion.to_dict()


def continue_discussion(discussion: Discussion, db: Database, content: str) -> dict:
    """Continue a concluded discussion with a new user contribution.

    Reopens the discussion, adds the user's message as a participant,
    and increases the round budget so the moderator picks it up.

    Args:
        discussion: The concluded discussion to continue.
        db: Database instance.
        content: The user's new message/prompt.

    Returns:
        ``discussion.to_dict()`` on success, or ``{"error": ...}`` dict.
    """
    if not discussion.id:
        return {"error": "No discussion loaded"}
    if discussion.status != "concluded":
        return {"error": "Discussion is not concluded"}
    if not content or not content.strip():
        return {"error": "Message content cannot be empty"}

    # Find the human participant (non-moderator) to attribute the message to.
    # Fall back to the moderator if no human participant exists.
    human_entity = None
    for e in discussion.entities:
        if e.entity_type.value == "human" and e.id != discussion.moderator_id:
            human_entity = e
            break
    if not human_entity:
        # Use moderator as fallback
        human_entity = discussion.moderator
    if not human_entity:
        return {"error": "No entity available to attribute message to"}

    # Increase round and cost budgets so the discussion can continue
    _increase_budgets(discussion, db)

    # Reopen: set active and restore turn state
    discussion.status = "active"
    discussion.is_active = True
    db.update_discussion(discussion.id, status="active", ended_at=None)

    # Restore turn state so the discussion can continue
    if discussion.turn_order:
        discussion.current_turn_index = 0
    discussion.turn_number = db.get_max_turn_number(discussion.id) + 1

    # Add system message about continuation
    mod_id = discussion.moderator_id or 0
    sys_msg = Message(
        entity_id=mod_id, entity_name="System",
        content="-- Discussion continued by user --",
        role=MessageRole.SYSTEM,
    )
    discussion.messages.append(sys_msg)
    db.add_message(
        discussion.id, mod_id,
        "-- Discussion continued by user --", "system",
        turn_number=discussion.turn_number,
    )

    # Add the user's message as a participant contribution
    user_msg = Message(
        entity_id=human_entity.id, entity_name=human_entity.name,
        content=content, role=MessageRole.PARTICIPANT,
    )
    discussion.messages.append(user_msg)
    db.add_message(
        discussion.id, human_entity.id, content, "participant",
        turn_number=discussion.turn_number,
    )

    return discussion.to_dict()


def reset_discussion(
    db: Database,
    key_resolver: Callable,
    tool_registry: Optional[object],
) -> tuple[Discussion, Moderator]:
    """Reset to a clean state for a new discussion.

    Args:
        db: Database instance.
        key_resolver: Callable used by :class:`Moderator` to resolve API keys.
        tool_registry: Tool registry passed to the new Moderator.

    Returns:
        A ``(Discussion, Moderator)`` tuple with fresh instances.
    """
    discussion = Discussion()
    moderator = Moderator(
        discussion, db,
        key_resolver=key_resolver,
        tool_registry=tool_registry,
    )
    return (discussion, moderator)
