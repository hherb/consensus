"""Participant roles — devil's-advocate assignment, tools and turn position.

Split out of ``app_discussion_setup`` (issue #61). Like that module, these
functions mutate a Discussion in-place and write to the database directly;
the ConsensusApp wrapper methods handle notification.
"""

import logging

from .app_discussion_flow.helpers import describe_internal_error, post_notice
from .app_discussion_rotation import (
    LIVE_STATUSES,
    snapshot_rotation,
    update_rotation,
)
from .database import Database
from .models import Discussion, Entity

logger = logging.getLogger(__name__)

# Tools auto-assigned to a devil's advocate entity
_DA_TOOL_NAMES = [
    "web_search", "fetch_webpage",
    "memory_store", "memory_recall", "memory_forget",
    "discussion_search", "kg_assert", "kg_query",
    "doc_add", "doc_list", "doc_get_length", "doc_get_text",
    "doc_get_sections", "doc_get_chapter", "doc_ask", "doc_summary",
]


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

    restore = snapshot_rotation(discussion)
    if participant_role == "devils_advocate":
        revert_other_devils_advocates(discussion, entity_id)

    discussion.member_roles[entity_id] = participant_role

    # Keep the DA last in every copy of the rotation (issue #104); the
    # roles are persisted with it, and only while live (never a concluded
    # record). A failed save restores memory (issue #147).
    error = update_rotation(discussion, db, restore)
    if error:
        return {"error": error}

    if participant_role == "devils_advocate":
        assign_da_tools(discussion, db, entity)

    return entity.to_dict()


def revert_other_devils_advocates(
    discussion: Discussion, entity_id: int,
) -> None:
    """Enforce a single devil's advocate: demote every DA but ``entity_id``.

    Changes memory only: the caller's ``update_rotation`` persists every
    role in the same transaction as the new DA (issue #107), so a failed
    swap cannot leave the record with no devil's advocate.
    """
    for eid, role in list(discussion.member_roles.items()):
        if role == "devils_advocate" and eid != entity_id:
            discussion.member_roles[eid] = "standard"


def assign_da_tools(discussion: Discussion, db: Database, entity: Entity) -> None:
    """Give a new devil's advocate its tools, after its role is saved.

    In a live discussion the role and rotation are already committed, so a
    failed tool write must not undo or misreport them: it is logged and
    shown as a transcript notice instead (golden rule 6).  Before start
    nothing is saved yet, so the error propagates to the caller.
    """
    try:
        auto_assign_da_tools(db, entity.id)
    except Exception as e:
        if not discussion.id or discussion.status not in LIVE_STATUSES:
            raise
        logger.exception(
            "Could not assign devil's-advocate tools to entity %s", entity.id)
        post_notice(discussion, db, entity,
                    f"-- Could not assign all devil's-advocate tools to "
                    f"{entity.name}: {describe_internal_error(e)} --")


def auto_assign_da_tools(db: Database, entity_id: int) -> None:
    """Assign web search and memory tools to a devil's advocate entity."""
    for tool_name in _DA_TOOL_NAMES:
        existing = db.get_entity_tool(entity_id, tool_name)
        if not existing:
            db.add_entity_tool(entity_id, tool_name, "private")

