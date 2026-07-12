"""Discussion setup and membership management — pure functions.

These functions mutate a Discussion in-place and interact with the database
directly. They do NOT call _notify(); the ConsensusApp wrapper methods handle
notification after calling each function.
"""

import time
from typing import Optional

from .app_discussion_flow import apply_method_turn_order
from .database import Database
from .methods import get_method, serialize_method_state
from .models import Discussion, Entity, EntityType, Message, MessageRole
from .moderator import Moderator

# Tools auto-assigned to a devil's advocate entity
_DA_TOOL_NAMES = [
    "web_search", "fetch_webpage",
    "memory_store", "memory_recall", "memory_forget",
    "discussion_search", "kg_assert", "kg_query",
    "doc_add", "doc_list", "doc_get_length", "doc_get_text",
    "doc_get_sections", "doc_get_chapter", "doc_ask", "doc_summary",
]


def add_to_discussion(
    discussion: Discussion,
    db: Database,
    entity_id: int,
    is_moderator: bool = False,
    also_participant: bool = False,
    participant_role: str = "standard",
) -> dict:
    """Add a saved entity to the current discussion.

    Returns the entity dict on success, or a dict with an ``"error"`` key.
    """
    row = db.get_entity(entity_id)
    if not row:
        return {"error": "Entity not found"}

    entity = Entity.from_db_row(row)

    if discussion.get_entity(entity_id):
        return {"error": f"{entity.name} is already in the discussion"}

    discussion.entities.append(entity)
    discussion.member_roles[entity_id] = participant_role

    if is_moderator:
        discussion.moderator_id = entity_id

    # Single-DA enforcement: revert any existing DA
    if participant_role == "devils_advocate":
        for eid, role in list(discussion.member_roles.items()):
            if role == "devils_advocate" and eid != entity_id:
                discussion.member_roles[eid] = "standard"
        auto_assign_da_tools(db, entity_id)

    # Persist to DB if discussion is already started
    if discussion.id and discussion.status in ("active", "paused"):
        next_pos = len(discussion.turn_order)
        db.add_discussion_member(
            discussion.id, entity_id,
            is_moderator=is_moderator,
            also_participant=True,
            turn_position=next_pos,
            participant_role=participant_role,
        )
        discussion.turn_order.append(entity_id)

        sys_msg = Message(
            entity_id=entity_id, entity_name=entity.name,
            content=f"-- {entity.name} joined the discussion --",
            role=MessageRole.SYSTEM,
        )
        discussion.messages.append(sys_msg)
        db.add_message(
            discussion.id, entity_id,
            f"-- {entity.name} joined the discussion --",
            "system", turn_number=discussion.turn_number,
        )

    return entity.to_dict()


def remove_from_discussion(
    discussion: Discussion,
    db: Database,
    entity_id: int,
) -> dict | bool:
    """Remove an entity from the current discussion.

    Returns ``True`` on success, or a dict with an ``"error"`` key.
    """
    # Guard: cannot remove moderator or current speaker mid-discussion
    if discussion.id and discussion.status in ("active", "paused"):
        if entity_id == discussion.moderator_id:
            return {"error": "Cannot remove the moderator"}
        current = discussion.current_speaker
        if (discussion.status == "active"
                and current and current.id == entity_id):
            return {"error": "Cannot remove the current speaker"}

    entity = discussion.get_entity(entity_id)
    entity_name = entity.name if entity else str(entity_id)

    # Adjust current_turn_index before removing from turn_order
    if entity_id in discussion.turn_order:
        removed_pos = discussion.turn_order.index(entity_id)
        discussion.turn_order.remove(entity_id)
        if removed_pos < discussion.current_turn_index:
            discussion.current_turn_index -= 1
        if discussion.turn_order:
            discussion.current_turn_index = (
                discussion.current_turn_index
                % len(discussion.turn_order)
            )
        else:
            discussion.current_turn_index = 0
        # The stamped turn index (see ``stamp_turn_index``) is a position
        # in the order it was recorded against — a shrunken order makes
        # it stale.  Drop it so a reload falls back to the id-based
        # last-speaker heuristic.
        discussion.method_state.pop("_turn_index", None)
        discussion.method_state.pop("_turn_index_turn", None)

    discussion.entities = [
        e for e in discussion.entities if e.id != entity_id
    ]
    discussion.member_roles.pop(entity_id, None)
    if discussion.moderator_id == entity_id:
        discussion.moderator_id = None

    # Persist to DB if discussion is already started
    if discussion.id and discussion.status in ("active", "paused"):
        db.remove_discussion_member(discussion.id, entity_id)
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )

        sys_msg = Message(
            entity_id=entity_id, entity_name=entity_name,
            content=f"-- {entity_name} left the discussion --",
            role=MessageRole.SYSTEM,
        )
        discussion.messages.append(sys_msg)
        db.add_message(
            discussion.id, entity_id,
            f"-- {entity_name} left the discussion --",
            "system", turn_number=discussion.turn_number,
        )

    return True


def set_moderator(
    discussion: Discussion,
    entity_id: int,
    also_participant: bool = False,
) -> bool:
    """Designate an entity as the moderator.

    Returns ``True`` if the entity was found and set, ``False`` otherwise.
    """
    entity = discussion.get_entity(entity_id)
    if entity:
        discussion.moderator_id = entity_id
        return True
    return False


def set_topic(discussion: Discussion, topic: str) -> bool:
    """Set the discussion topic.

    Returns ``True`` unconditionally.
    """
    discussion.topic = topic
    return True


def set_participant_role(
    discussion: Discussion,
    db: Database,
    entity_id: int,
    participant_role: str = "standard",
) -> dict:
    """Set or change a participant's role (e.g. devils_advocate).

    Only one devil's advocate is allowed per discussion. Setting a new
    DA reverts the previous one to standard. The DA is always moved to
    the end of the turn order.

    Returns the entity dict on success, or a dict with an ``"error"`` key.
    """
    entity = discussion.get_entity(entity_id)
    if not entity:
        return {"error": "Entity not in discussion"}
    if entity_id == discussion.moderator_id:
        return {"error": "Cannot assign a role to the moderator"}

    # Single-DA enforcement: revert any existing DA
    if participant_role == "devils_advocate":
        for eid, role in list(discussion.member_roles.items()):
            if role == "devils_advocate" and eid != entity_id:
                discussion.member_roles[eid] = "standard"
                if discussion.id:
                    db.update_member_role(
                        discussion.id, eid, "standard")

    discussion.member_roles[entity_id] = participant_role

    # Auto-assign tools for DA
    if participant_role == "devils_advocate":
        auto_assign_da_tools(db, entity_id)

    # Move DA to end of turn order (or restore normal position)
    if entity_id in discussion.turn_order:
        reorder_da_in_turn_order(discussion)

    # Persist role to DB if discussion is active
    if discussion.id:
        db.update_member_role(
            discussion.id, entity_id, participant_role)

    return entity.to_dict()


def auto_assign_da_tools(db: Database, entity_id: int) -> None:
    """Assign web search and memory tools to a devil's advocate entity."""
    for tool_name in _DA_TOOL_NAMES:
        existing = db.get_entity_tool(entity_id, tool_name)
        if not existing:
            db.add_entity_tool(entity_id, tool_name, "private")


def reorder_da_in_turn_order(discussion: Discussion) -> None:
    """Ensure devil's advocate entity is last in turn order."""
    da_id = None
    for eid, role in discussion.member_roles.items():
        if role == "devils_advocate" and eid in discussion.turn_order:
            da_id = eid
            break
    if da_id is None:
        return
    if da_id in discussion.turn_order:
        discussion.turn_order.remove(da_id)
        discussion.turn_order.append(da_id)
        # Keep current_turn_index valid
        if discussion.turn_order:
            discussion.current_turn_index = (
                discussion.current_turn_index
                % len(discussion.turn_order)
            )


def set_discussion_method(
    discussion: Discussion,
    method_name: str,
) -> dict:
    """Set the discussion method (must be called before starting).

    Returns a dict with method info.
    Raises ``ValueError`` if the method is unknown or the discussion
    has already started.
    """
    if discussion.status != "setup":
        raise ValueError("Cannot change method after discussion has started")
    try:
        method = get_method(method_name)
    except KeyError:
        raise ValueError(f"Unknown discussion method: {method_name!r}")
    discussion.discussion_method = method_name
    return method.to_dict()


async def recommend_method(
    topic: str,
    answer_type: str,
    ai_client,
    provider: dict,
) -> list[dict]:
    """Get LLM-based method recommendations for a topic.

    Returns a list of recommendation dicts.
    """
    from .methods import list_methods
    from .methods.recommender import MethodRecommender

    recommender = MethodRecommender()
    catalog = list_methods()
    recommendations = await recommender.recommend(
        topic=topic,
        answer_type=answer_type,
        method_catalog=catalog,
        ai_client=ai_client,
        provider=provider,
    )
    return [r.to_dict() for r in recommendations]


def start_discussion(
    discussion: Discussion,
    db: Database,
    moderator: Moderator,
    moderator_participates: bool = False,
    max_rounds: int = 0,
    cost_limit: float = 0.0,
) -> dict:
    """Start a new discussion with the configured entities and topic.

    Returns ``{"started": True}`` on success, or a dict with an ``"error"``
    key on validation failure. The caller (ConsensusApp) appends full state
    via ``get_state()`` after a successful start.
    """
    if not discussion.topic:
        return {"error": "No topic set"}
    if len(discussion.entities) < 2:
        return {"error": "Need at least 2 participants"}
    if not discussion.moderator_id:
        return {"error": "No moderator designated"}

    mod = discussion.moderator
    if not mod:
        return {"error": "Moderator entity not found"}

    # Validate all entities still exist in the database
    for e in discussion.entities:
        if not db.get_entity(e.id):
            return {"error": f"Entity '{e.name}' (id={e.id}) no longer exists"}

    # Court of Law role validation (before DB writes)
    if discussion.discussion_method == "court_of_law":
        roles = discussion.member_roles
        has_prosecutor = any(r == "prosecutor" for r in roles.values())
        has_plaintiff = any(r == "plaintiff" for r in roles.values())
        has_defense = any(r == "defense" for r in roles.values())
        if not has_prosecutor and not has_plaintiff:
            return {"error": "Court of Law requires at least one Prosecutor "
                    "(criminal) or Plaintiff (civil) participant"}
        if not has_defense:
            return {"error": "Court of Law requires at least one Defense "
                    "participant"}

    # Clear any stale state from a previous discussion
    discussion.messages.clear()
    discussion.storyboard.clear()
    discussion.turn_order.clear()

    # Create DB record
    did = db.create_discussion(
        discussion.topic, discussion.moderator_id,
    )
    discussion.id = did
    db.update_discussion(did, status="active", started_at=time.time())

    # Link images uploaded during setup to the new discussion
    for image_id in discussion.pending_image_ids:
        db.add_discussion_image(did, image_id)
    discussion.pending_image_ids.clear()

    # Build turn order -- DA goes last
    da_entity = None
    turn_pos = 0
    for e in discussion.entities:
        is_mod = e.id == discussion.moderator_id
        in_rotation = not is_mod or moderator_participates
        role = discussion.member_roles.get(e.id, "standard")
        if role == "devils_advocate" and in_rotation:
            da_entity = e  # defer to end
            continue
        db.add_discussion_member(
            did, e.id,
            is_moderator=is_mod,
            also_participant=moderator_participates if is_mod else True,
            turn_position=turn_pos if in_rotation else None,
            participant_role=role,
        )
        if in_rotation:
            discussion.turn_order.append(e.id)
            turn_pos += 1
    # Append DA last
    if da_entity:
        is_mod = da_entity.id == discussion.moderator_id
        in_rotation = not is_mod or moderator_participates
        role = discussion.member_roles.get(da_entity.id, "standard")
        db.add_discussion_member(
            did, da_entity.id,
            is_moderator=is_mod,
            also_participant=moderator_participates if is_mod else True,
            turn_position=turn_pos if in_rotation else None,
            participant_role=role,
        )
        if in_rotation:
            discussion.turn_order.append(da_entity.id)
            turn_pos += 1

    discussion.base_turn_order = list(discussion.turn_order)
    discussion.current_turn_index = 0
    discussion.turn_number = 1
    discussion.max_rounds = max_rounds
    discussion.cost_limit = cost_limit
    discussion.is_active = True
    discussion.status = "active"

    # Persist max_rounds, cost_limit, and context strategy defaults
    updates = {}
    if max_rounds > 0:
        updates["max_rounds"] = max_rounds
    if cost_limit > 0:
        updates["cost_limit"] = cost_limit
    updates["default_context_strategy"] = discussion.default_context_strategy
    updates["default_context_window_size"] = discussion.default_context_window_size
    db.update_discussion(did, **updates)

    # Flush per-member context strategy overrides set during setup
    for entity_id, cfg in discussion.member_context_configs.items():
        db.update_member_context_strategy(
            did, entity_id,
            cfg.get("strategy", "sliding_window"),
            cfg.get("window_size", 20),
        )

    # Initialise method state
    try:
        method = get_method(discussion.discussion_method)
        discussion.method_state = method.init_state(discussion)
        # Apply the first phase's turn order so phase-1 ordering (e.g. red
        # team exclusion, humans-only intake) takes effect from turn 1
        # rather than only after the first round completes (issue #13).
        # Must run before the persist below: deriving the order can write
        # method_state (``_turn_order``, red-team designation) that has to
        # survive a reload before the first turn completes (issue #16).
        apply_method_turn_order(discussion)
        db.update_discussion(
            did,
            discussion_method=discussion.discussion_method,
            method_state=serialize_method_state(discussion.method_state),
        )
    except KeyError:
        pass  # open_discussion — no special state needed

    # Opening message from moderator
    target_type = "ai" if mod.entity_type == EntityType.AI else "human"
    open_prompt = moderator.resolve_prompt(
        "moderator", target_type, "open",
        entity_name=mod.name,
        topic=discussion.topic,
        participants=", ".join(
            e.name for e in discussion.entities if e.id != mod.id
        ),
    )
    if not open_prompt:
        open_prompt = (
            f"Welcome to this discussion on: **{discussion.topic}**\n\n"
            f"Let's begin."
        )

    if max_rounds > 0:
        open_prompt += (
            f"\n\n**Note:** This discussion is limited to "
            f"**{max_rounds} round{'s' if max_rounds != 1 else ''}**. "
            f"Please make your contributions count — be thorough and "
            f"concise. A conclusion will be drawn after the final round."
        )

    if cost_limit > 0:
        open_prompt += (
            f"\n\n**Budget:** This discussion has a cost limit of "
            f"**${cost_limit:.2f}**."
        )

    opening = Message(
        entity_id=mod.id, entity_name=mod.name,
        content=open_prompt, role=MessageRole.MODERATOR,
    )
    discussion.messages.append(opening)
    db.add_message(
        did, mod.id, open_prompt, "moderator",
        turn_number=0,
    )

    return {"started": True}
