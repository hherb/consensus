"""Discussion-method switching — the whole Triage lifecycle.

Triage runs as an ordinary method whose final act is to hand the
discussion over to the method it recommended.  Everything that lifecycle
needs lives here: the recommender call that fills Triage's
``recommendations``, the switch itself (including the tool-capability
gate), the blocked-switch recovery path the UI retries through, and
:func:`refresh_ai_configs`, which that recovery path needs to pick up
profile edits made while the discussion was paused.

``turns`` enters this module at two points, at opposite ends of a Triage
run:

- :func:`run_triage_recommender`, called from ``generate_ai_turn`` while
  the ``recommend`` phase is live — *not* part of the handoff itself.
- :func:`handle_triage_handoff`, called from ``complete_turn`` once the
  Triage run ends.  It returns the result dict the caller passes straight
  back to the frontend, or ``None`` when this was not a handoff.

:func:`refresh_ai_configs` is re-exported by the package but has no caller
outside :func:`retry_method_switch`; it is public because the blocked-switch
recovery dialog's contract depends on it.
"""

import logging
from typing import Callable

from ..app_discussion_state import pause_discussion, resume_discussion
from ..database import Database
from ..methods import get_method, serialize_method_state
from ..models import Discussion, Entity, EntityType, Message, MessageRole
from ..structured_output import (
    _format_tool_block_error, find_tool_blocked_entities,
)
from .helpers import (
    apply_method_turn_order, describe_flow_error, stamp_turn_index,
)

logger = logging.getLogger(__name__)

#: Method the Triage flow falls back to when no recommendation could be
#: produced.  It is a fallback, never a recommendation — every path that
#: sets it also records ``recommender_error`` so the two stay
#: distinguishable (issue #72).
RECOMMENDER_FALLBACK_METHOD = "open_discussion"

#: Appended to the recommend-phase transcript message when the classifier
#: failed, so the user can see the default was forced rather than chosen.
RECOMMENDER_FAILURE_NOTICE = (
    "\n\n---\n\n**Automatic method recommendation failed.** {detail}\n\n"
    f"`{RECOMMENDER_FALLBACK_METHOD}` is offered as a fallback — this is a "
    "failure, not a recommendation. Please name the method you want in the "
    "confirmation phase, or fix the moderator's model configuration and "
    "restart the triage."
)

#: Recorded when the triage moderator has no AI configuration at all, so
#: the classifier could never have run.
_NO_AI_CONFIG_DETAIL = (
    "The triage moderator ({name}) has no AI configuration, so the method "
    "classifier could not be run."
)


def _record_recommender_failure(state: dict, detail: str) -> str:
    """Record a recommender failure in ``method_state`` and return ``detail``.

    Both failure paths write the same three keys so the confirm phase can
    always tell "the classifier ran and chose this" from "the classifier
    never produced anything" (issue #72).
    """
    state["recommender_error"] = detail
    state["recommendations"] = []
    state["recommended_method"] = RECOMMENDER_FALLBACK_METHOD
    return detail


async def run_triage_recommender(
    discussion: Discussion, moderator_entity: Entity, key_resolver,
) -> str | None:
    """Call MethodRecommender after the triage moderator's synthesis turn.

    Returns a user-facing description of the failure, or ``None`` when the
    classifier ran.  A failure is also recorded in ``method_state`` so it
    survives a reload and reaches the confirm-phase prompts; the caller
    posts it into the transcript (golden rule 6, issue #72).
    """
    from ..ai_client import AIClient
    from ..methods import list_methods
    from ..methods.recommender import MethodRecommender

    state = discussion.method_state
    characterization = state.get("moderator_characterization", "")
    if not moderator_entity.ai_config:
        logger.warning(
            "Triage recommender skipped: moderator entity %s (%s) has no "
            "ai_config; discussion %s falls back to %s",
            moderator_entity.id, moderator_entity.name, discussion.id,
            RECOMMENDER_FALLBACK_METHOD,
        )
        return _record_recommender_failure(
            state, _NO_AI_CONFIG_DETAIL.format(name=moderator_entity.name))

    api_key = key_resolver(
        moderator_entity.ai_config.provider_id,
        "",  # env var looked up by resolver
    )
    ai_client = AIClient(
        base_url=moderator_entity.ai_config.base_url,
        api_key=api_key,
    )
    provider = {"model": moderator_entity.ai_config.model}

    recommender = MethodRecommender()
    try:
        recs = await recommender.recommend(
            topic=discussion.topic,
            answer_type="",
            method_catalog=list_methods(),
            ai_client=ai_client,
            provider=provider,
            additional_context=characterization,
        )
        state["recommendations"] = [r.to_dict() for r in recs]
        state["recommended_method"] = recs[0].method_name if recs else None
        # Clear a failure recorded by an earlier attempt, so a retry that
        # succeeds does not leave a stale warning in the prompts.
        state.pop("recommender_error", None)
        return None
    except Exception as e:
        logger.exception("Triage recommender call failed")
        return _record_recommender_failure(state, describe_flow_error(e))
    finally:
        await ai_client.close()


def switch_discussion_method(
    discussion: Discussion, db: Database, method_name: str,
) -> dict:
    """Switch the discussion to a new method (used by triage).

    Reinitializes method_state, persists to DB, and adds a system
    message announcing the transition. Returns the new method's
    metadata dict, or an error dict.

    Runs the same tool-capability gate as discussion setup (issue #23)
    before any mutation: a structured target method whose panel models
    are known to lack tool support is rejected, leaving the discussion's
    method, method_state, and messages untouched. Triage records a pending
    switch and pauses when this returns an error (spec 2026-07-17); the error
    dict carries ``blocked_entities`` so the recovery UI can name the offenders.
    """
    if method_name == "triage":
        return {"error": "Cannot switch to triage method"}

    try:
        method = get_method(method_name)
    except KeyError:
        return {"error": f"Unknown method: {method_name!r}"}

    # Tool-capability gate (issue #23), scanned once: the target method
    # hasn't been assigned to discussion.discussion_method yet, so it must
    # be passed explicitly (the default falls back to the discussion's
    # *current* method, which is wrong here). One scan feeds both the
    # error message and the structured offender list the recovery UI needs.
    blocked_entities = find_tool_blocked_entities(
        discussion, db, method_name)
    if blocked_entities:
        return {
            "error": _format_tool_block_error(blocked_entities, method),
            "blocked_entities": blocked_entities,
        }

    # Budget bookkeeping written by _increase_budgets must survive the
    # method_state reset (issue #16).
    preserved = {
        key: discussion.method_state[key]
        for key in ("_continuation_count", "_original_max_rounds",
                    "_original_cost_limit")
        if key in discussion.method_state
    }
    discussion.discussion_method = method_name
    discussion.method_state = method.init_state(discussion)
    discussion.method_state.update(preserved)

    if discussion.id:
        db.update_discussion(
            discussion.id,
            discussion_method=method_name,
            method_state=serialize_method_state(discussion.method_state),
        )

    # System message announcing the transition
    first_phase = method.default_phases[0] if method.default_phases else None
    phase_info = f" Beginning {first_phase.display_name} phase." if first_phase else ""
    transition_text = (
        f"**Discussion method set to {method.display_name}.**{phase_info}"
    )
    mod = discussion.moderator
    if mod and discussion.id:
        msg = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=transition_text, role=MessageRole.SYSTEM,
        )
        discussion.messages.append(msg)
        db.add_message(
            discussion.id, mod.id, transition_text, "system",
            turn_number=discussion.turn_number,
        )

    return method.to_dict()


def refresh_ai_configs(discussion: Discussion, db: Database) -> None:
    """Reload each AI member's profile from the DB onto the live objects.

    Profile edits made while a discussion is loaded (e.g. fixing a
    non-tool-capable model from the blocked-switch recovery dialog)
    only touch the database row; the in-memory Entity keeps the
    AIConfig snapshot taken when it joined.  Swap in a fresh snapshot,
    preserving entity identity and roster order.  Rows that no longer
    exist are skipped.
    """
    for entity in discussion.entities:
        if entity.entity_type != EntityType.AI:
            continue
        row = db.get_entity(entity.id)
        if not row:
            continue
        entity.ai_config = Entity.from_db_row(row).ai_config


def retry_method_switch(
    discussion: Discussion, db: Database,
    get_state_fn: Callable[[], dict],
) -> dict:
    """Retry a Triage handoff blocked by the tool-capability gate.

    Refreshes AI members' profiles from the DB (so a model fix made in
    the UI takes effect), re-runs the switch, and resumes the paused
    discussion on success (spec 2026-07-17).  Returns the same
    ``method_switched`` shape ``complete_turn`` produces for an
    unblocked handoff, the ``method_switch_blocked`` shape when still
    blocked, or an error dict when there is nothing to retry.
    """
    if not discussion.id:
        return {"error": "No active discussion"}
    if discussion.status == "concluded":
        return {"error": "Discussion is already concluded"}
    if discussion.discussion_method != "triage":
        return {"error": "No pending method switch to retry"}
    pending = discussion.method_state.get("_pending_method_switch")
    if not pending:
        return {"error": "No pending method switch to retry"}

    refresh_ai_configs(discussion, db)
    chosen = pending["target_method"]
    switch_result = switch_discussion_method(discussion, db, chosen)
    if "error" in switch_result:
        switch_error = switch_result["error"]
        blocked_entities = switch_result.get("blocked_entities", [])
        logger.warning(
            "Retry of method switch for discussion %s to %r still "
            "blocked: %s", discussion.id, chosen, switch_error,
        )
        # Keep the pending record fresh for the dialog; the transcript
        # notice is NOT reposted (same target — _switch_error_posted).
        discussion.method_state["_pending_method_switch"] = {
            "target_method": chosen,
            "switch_error": switch_error,
            "blocked_entities": blocked_entities,
        }
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )
        # A manually-resumed discussion must not keep running behind
        # the recovery dialog — re-pause it (final-review finding #2).
        if discussion.status == "active":
            pause_discussion(discussion, db)
        return {
            "method_switch_blocked": True,
            "switch_error": switch_error,
            "target_method": chosen,
            "blocked_entities": blocked_entities,
            "turn_number": discussion.turn_number,
            "current_round": discussion.current_round,
            "state": get_state_fn(),
        }

    # Success. _pending_method_switch was wiped by init_state — it is
    # deliberately NOT in switch_discussion_method's preserved set.
    if discussion.status == "paused":
        resume_discussion(discussion, db)
    # Mirror complete_turn's successful-handoff path: the new method's
    # first phase reorders turns from the full roster.
    apply_method_turn_order(discussion, reset_index=True)
    stamp_turn_index(discussion)
    db.update_discussion(
        discussion.id,
        method_state=serialize_method_state(discussion.method_state),
    )
    return {
        "method_switched": True,
        "new_method": switch_result,
        "turn_number": discussion.turn_number,
        "current_round": discussion.current_round,
        "state": get_state_fn(),
    }



def handle_triage_handoff(
    discussion: Discussion, db: Database,
    get_state_fn: Callable[[], dict],
) -> dict | None:
    """Hand a finished Triage run over to the method it chose.

    Called by ``complete_turn`` when the active method has ended.  Returns
    ``None`` when this is not a Triage handoff (the caller then reports a
    plain ``method_complete``), the ``method_switched`` result on success,
    or the ``method_switch_blocked`` result when the tool-capability gate
    rejected the target — in which case the discussion is paused for the
    recovery dialog rather than concluded, and the failure is both logged
    and posted into the transcript (golden rule 6).
    """
    chosen = discussion.method_state.get("chosen_method")
    if discussion.discussion_method != "triage" or not chosen:
        return None

    mod = discussion.moderator
    switch_result = switch_discussion_method(discussion, db, chosen)
    if "error" not in switch_result:
        # Reorder turns for the new method's first phase, starting from
        # the full roster — the triage phases ran moderator-only and must
        # not leak that order.
        apply_method_turn_order(discussion, reset_index=True)
        if discussion.id:
            stamp_turn_index(discussion)
            db.update_discussion(
                discussion.id,
                method_state=serialize_method_state(discussion.method_state),
            )
        return {
            "method_switched": True,
            "new_method": switch_result,
            "turn_number": discussion.turn_number,
            "current_round": discussion.current_round,
            "state": get_state_fn(),
        }

    # Blocked switch (e.g. the tool-capability gate, issue #23): the error
    # must be loud — logged and posted into the transcript, never silently
    # swallowed into a bare method_complete (golden rule 6).  Post the
    # notice at most once *per target method*: complete_turn re-enters this
    # branch while the frontend concludes, but a later blocked switch to a
    # different method is new information.
    switch_error = switch_result["error"]
    blocked_entities = switch_result.get("blocked_entities", [])
    logger.warning(
        "Triage could not switch discussion %s to %r: %s",
        discussion.id, chosen, switch_error,
    )
    # Record the pending switch so the user can fix the offending model and
    # retry (spec 2026-07-17); the record survives reload via method_state
    # and is wiped by init_state on a later successful switch.
    discussion.method_state["_pending_method_switch"] = {
        "target_method": chosen,
        "switch_error": switch_error,
        "blocked_entities": blocked_entities,
    }
    # Scalar last-target key, deliberately: after an intervening blocked
    # switch to a different method, re-notifying about an earlier target
    # again is new information, not spam.
    already_notified = discussion.method_state.get("_switch_error_posted")
    if mod and discussion.id and already_notified != chosen:
        discussion.method_state["_switch_error_posted"] = chosen
        notice = (
            f"**The recommended method could not be adopted.** {switch_error}"
        )
        sys_msg = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=notice, role=MessageRole.SYSTEM,
        )
        discussion.messages.append(sys_msg)
        db.add_message(
            discussion.id, mod.id, notice, "system",
            turn_number=discussion.turn_number,
        )
    if discussion.id:
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )
    # Pause instead of concluding: the frontend shows a recovery dialog
    # and retries via retry_method_switch.
    if discussion.status == "active":
        pause_discussion(discussion, db)
    return {
        "method_switch_blocked": True,
        "switch_error": switch_error,
        "target_method": chosen,
        "blocked_entities": blocked_entities,
        "turn_number": discussion.turn_number,
        "current_round": discussion.current_round,
        "state": get_state_fn(),
    }
