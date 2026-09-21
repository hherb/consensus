"""Turn execution — AI generation, turn completion, and reassignment.

``generate_ai_turn`` produces one participant contribution;
``complete_turn`` closes the turn (moderator summary, storyboard entry,
rotation advance, method round/phase lifecycle) and reports what the
frontend should do next.  Method switching and the mediation/conclusion
paths live in their sibling modules.
"""

import json
import logging
from typing import Callable

from ..database import Database
from ..evidence import record_and_annotate_evidence
from ..methods import get_active_method, serialize_method_state
from ..models import (
    Discussion, EntityType, Message, MessageRole, StoryboardEntry,
)
from ..moderator import Moderator
from ..pricing import PricingCache
from .helpers import (
    apply_method_turn_order, calculate_discussion_cost, describe_flow_error,
    describe_internal_error, describe_turn_error, is_pass, is_provider_error,
    post_notice, stamp_turn_index,
)
from .method_switch import (
    RECOMMENDER_FAILURE_NOTICE, handle_triage_handoff, run_triage_recommender,
)

logger = logging.getLogger(__name__)

#: Skip notice for a failure that came from the provider or the network —
#: something the user can act on by changing model, key or budget.
_PROVIDER_SKIP_NOTICE = (
    "*{name} could not respond due to an API error ({detail}). "
    "Skipping to the next participant.*"
)

#: Skip notice for a failure inside Consensus itself.  ``generate_ai_turn``
#: wraps method handlers, serialisation and DB writes as well as the
#: provider call, so these must not be dressed up as provider problems —
#: that sends debugging in exactly the wrong direction (issue #74).
_INTERNAL_SKIP_NOTICE = (
    "*{name}'s turn failed due to an internal error ({detail}). This is a "
    "fault in Consensus, not in the AI provider — please report it. "
    "Skipping to the next participant.*"
)


async def generate_ai_turn(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache, key_resolver=None,
) -> dict:
    """Generate an AI participant's contribution for the current turn.

    Returns a dict with the message data (including optional 'passed',
    'warning', 'error', and 'skipped' keys).
    """
    if not discussion.is_active or discussion.status == "concluded":
        return {"error": "Discussion is not active"}
    current = discussion.current_speaker
    if not current:
        return {"error": "No current speaker"}
    if current.entity_type != EntityType.AI:
        return {"error": f"{current.name} is human - waiting for input"}

    # Pre-flight cost limit check
    if discussion.cost_limit > 0:
        total = calculate_discussion_cost(discussion)
        if total >= discussion.cost_limit:
            return {"cost_limit_reached": True, "total_cost": total,
                    "cost_limit": discussion.cost_limit}

    try:
        participant_role = discussion.member_roles.get(
            current.id, "standard")
        resp = await moderator.generate_turn(
            current, participant_role=participant_role)

        # Detect if the participant chose to pass.  A validated
        # structured payload is never a pass — its content field is
        # incidental side text next to the forced tool call (issue #23).
        passed = resp.structured_output is None and is_pass(resp.content)

        # Method-specific response post-processing
        method = get_active_method(discussion)
        if method and not passed:
            if resp.structured_output is not None:
                # Forced-tool path (issue #23): the payload was already
                # validated by the method's validate_output hook.
                processed = method.process_structured_response(
                    resp.structured_output, current, discussion)
            else:
                processed = method.process_response(
                    resp.content, current, discussion)
            content = processed.display_content
            phase = method.current_phase(discussion)
            if phase is not None and phase.track_evidence:
                content = record_and_annotate_evidence(
                    discussion, current, discussion.turn_number, content,
                    resp.tool_calls)
            # Persist updated method_state
            if discussion.id:
                db.update_discussion(
                    discussion.id,
                    method_state=serialize_method_state(discussion.method_state),
                )
            # Triage recommend phase: run async MethodRecommender
            if (discussion.discussion_method == "triage"
                    and discussion.method_state.get("current_phase") == "recommend"
                    and key_resolver):
                rec_error = await run_triage_recommender(
                    discussion, current, key_resolver)
                if rec_error:
                    # The recommend phase's whole purpose is to pick a
                    # method; when the classifier fails the user must see
                    # that the default was forced, not chosen (golden
                    # rule 6, issue #72).  Appending to this turn's own
                    # content puts it where the recommendation would have
                    # been, and it persists with the message below.
                    content += RECOMMENDER_FAILURE_NOTICE.format(
                        detail=rec_error)
                if discussion.id:
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
        else:
            content = resp.content

        if passed:
            content = f"*{current.name} passed this round.*"
        elif not (content or "").strip():
            # A reasoning model can spend its entire completion budget on
            # hidden thinking and return no visible text (observed:
            # claude-sonnet-5 at max_tokens=1024 in a 43k-token context).
            # Render an explanatory notice — never a silent empty bubble
            # (golden rule 6).
            capped = resp.finish_reason == "length"
            detail = (
                " — it hit its max_tokens limit before emitting any text; "
                "reasoning models may need a larger completion budget"
                if capped else ""
            )
            content = f"*{current.name} produced no visible output{detail}.*"
            if not resp.warning:
                resp.warning = (
                    f"{current.name} produced no visible output"
                    + (f" — consider raising max_tokens for {resp.model}"
                       if capped else "")
                )

        # Serialize tool call records if any
        tool_calls_json = ""
        if resp.tool_calls:
            tool_calls_json = json.dumps(
                [tc.to_dict() for tc in resp.tool_calls]
            )

        cost = pricing.calculate_cost_with_refresh(
            resp.model,
            current.ai_config.base_url if current.ai_config else "",
            resp.prompt_tokens,
            resp.completion_tokens,
        )

        msg = Message(
            entity_id=current.id, entity_name=current.name,
            content=content, role=MessageRole.PARTICIPANT,
            model_used=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            latency_ms=resp.latency_ms,
            cost=cost,
            tool_calls_json=tool_calls_json,
        )
        discussion.messages.append(msg)

        prompt_id = moderator.prompt_id("participant", "ai", "turn")
        db.add_message(
            discussion.id, current.id, content, "participant",
            turn_number=discussion.turn_number,
            model_used=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            latency_ms=resp.latency_ms,
            temperature_used=current.ai_config.temperature if current.ai_config else None,
            prompt_id=prompt_id,
            tool_calls_json=tool_calls_json,
            cost=cost,
        )
        result = msg.to_dict()
        if passed:
            result["passed"] = True
        if resp.warning:
            result["warning"] = resp.warning
        return result
    except Exception as e:
        logger.exception("AI turn failed for %s", current.name)
        # Post a visible notification so the moderator/participants know
        # this participant was skipped — and name the *kind* of failure
        # honestly.  This block wraps far more than the provider call
        # (method handlers, evidence annotation, cost lookup, two DB
        # writes), so the notice is chosen by classification rather than
        # assumed to be an API error (issue #74).
        provider_fault = is_provider_error(e)
        detail = (describe_turn_error(e) if provider_fault
                  else describe_internal_error(e))
        template = (_PROVIDER_SKIP_NOTICE if provider_fault
                    else _INTERNAL_SKIP_NOTICE)
        error_notice = template.format(name=current.name, detail=detail)
        # post_notice guards the write: when the DB is what failed, an
        # unguarded add_message here raises a second exception out of this
        # handler and the user sees nothing at all.
        msg = post_notice(
            discussion, db, current, error_notice,
            role=MessageRole.PARTICIPANT,
        )
        result = msg.to_dict()
        result["error"] = detail
        result["error_kind"] = "provider" if provider_fault else "internal"
        result["skipped"] = True
        return result


async def complete_turn(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache, get_state_fn: Callable[[], dict],
    moderator_summary: str = "",
) -> dict:
    """Complete the current turn: generate or accept summary, advance turn order.

    The ``get_state_fn`` callable is invoked when a full state snapshot is
    needed in the return value (e.g. awaiting moderator summary, max rounds
    reached, or normal completion).
    """
    if not discussion.is_active or discussion.status == "concluded":
        return {"error": "Discussion is not active"}
    mod = discussion.moderator
    summary_text = ""

    # Capture the current speaker before summary generation changes messages
    current = discussion.current_speaker
    speaker_name = current.name if current else "Unknown"
    speaker_id = current.id if current else 0

    # Check if the most recent participant message was a pass.  Scan back for
    # the last PARTICIPANT message rather than trusting messages[-1], which
    # may be a system/moderator message appended after the turn.
    last_participant_msg = next(
        (m for m in reversed(discussion.messages)
         if m.role == MessageRole.PARTICIPANT),
        None,
    )
    participant_passed = (last_participant_msg is not None
                          and is_pass(last_participant_msg.content))

    if participant_passed and mod:
        # No AI summary needed — just note the pass
        summary_text = f"{speaker_name} passed this round."
        db.add_message(
            discussion.id, mod.id, summary_text, "moderator",
            turn_number=discussion.turn_number,
        )
    elif mod and mod.entity_type == EntityType.AI and not participant_passed:
        try:
            next_entity = moderator.peek_next_speaker()
            next_name = next_entity.name if next_entity else ""
            resp = await moderator.generate_summary(
                next_speaker_name=next_name)
            summary_text = resp.content
            if summary_text:
                prompt_id = moderator.prompt_id(
                    "moderator", "ai", "summarize",
                )
                cost = pricing.calculate_cost_with_refresh(
                    resp.model,
                    mod.ai_config.base_url if mod.ai_config else "",
                    resp.prompt_tokens,
                    resp.completion_tokens,
                )
                db.add_message(
                    discussion.id, mod.id, summary_text, "moderator",
                    turn_number=discussion.turn_number,
                    model_used=resp.model,
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    total_tokens=resp.total_tokens,
                    latency_ms=resp.latency_ms,
                    prompt_id=prompt_id,
                    cost=cost,
                )
        except Exception as e:
            logger.exception("AI summary generation failed")
            # Same classification as the turn path: this block also wraps
            # a cost lookup and a DB write, not just the provider call.
            return {
                "error":
                    f"Summary generation failed: {describe_flow_error(e)}",
            }
    elif mod and moderator_summary:
        summary_text = moderator_summary
        db.add_message(
            discussion.id, mod.id, summary_text, "moderator",
            turn_number=discussion.turn_number,
        )
    elif not mod:
        return {"error": "No moderator designated"}
    else:
        return {
            "awaiting_moderator_summary": True,
            "state": get_state_fn(),
        }

    if summary_text:
        entry = StoryboardEntry(
            turn_number=discussion.turn_number,
            summary=summary_text,
            speaker_name=speaker_name,
        )
        discussion.storyboard.append(entry)

        db.add_storyboard_entry(
            discussion.id, discussion.turn_number,
            summary_text, speaker_id,
        )

        summary_msg = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=summary_text, role=MessageRole.MODERATOR,
        )
        discussion.messages.append(summary_msg)

    # The returned entity is deliberately discarded: a phase transition
    # below can reorder ``turn_order`` and reset the index, so the
    # speaker is recomputed from live state at the end instead.
    moderator.advance_turn()

    # Method phase management
    method = get_active_method(discussion)
    if method:
        # Detect full-round completion: advance_turn() above already
        # incremented current_turn_index and wrapped it modulo turn_order
        # length.  When it wraps back to 0 (and we're past turn 1), all
        # participants have spoken this round.
        if (discussion.turn_order
                and discussion.current_turn_index == 0
                and discussion.turn_number > 1):
            method.on_round_complete(discussion)
            # A handler may change its intra-phase turn order on round
            # completion (e.g. the Court huddle sub-state machine advancing
            # from team-huddle to spokesperson-speaks).  Re-apply it now so the
            # change takes effect immediately instead of only at the next phase
            # transition.  Static handlers return the order unchanged.
            apply_method_turn_order(discussion)

        # Check for phase transition
        if method.should_advance_phase(discussion):
            new_phase = method.advance_phase(discussion)
            if new_phase:
                # Let the method reorder turns for the new phase; the
                # transition is a rotation boundary, so restart at index 0
                # even when the order is unchanged (issue #19).
                apply_method_turn_order(discussion, reset_index=True)

                # Post phase transition message
                transition_msg = method.get_phase_transition_message(
                    new_phase, discussion)
                if transition_msg and mod:
                    sys_msg = Message(
                        entity_id=mod.id, entity_name=mod.name,
                        content=transition_msg, role=MessageRole.SYSTEM,
                    )
                    discussion.messages.append(sys_msg)
                    db.add_message(
                        discussion.id, mod.id, transition_msg, "system",
                        turn_number=discussion.turn_number,
                    )
            else:
                # Method ended: linear order exhausted, or a next_phase
                # hook aborted it early.  Post the method's explanation
                # (if any) so the user learns WHY it ended (issue #30).
                # Skip it when Triage is about to hand off to its chosen
                # method (a switch, not an end), and post at most once —
                # complete_turn can hit this branch again if turns keep
                # completing after method_complete was already returned.
                switching = (discussion.discussion_method == "triage"
                             and discussion.method_state.get("chosen_method"))
                already_posted = discussion.method_state.get(
                    "_complete_message_posted")
                end_msg = ("" if switching or already_posted
                           else method.get_method_complete_message(discussion))
                if end_msg and mod:
                    discussion.method_state["_complete_message_posted"] = True
                    sys_msg = Message(
                        entity_id=mod.id, entity_name=mod.name,
                        content=end_msg, role=MessageRole.SYSTEM,
                    )
                    discussion.messages.append(sys_msg)
                    db.add_message(
                        discussion.id, mod.id, end_msg, "system",
                        turn_number=discussion.turn_number,
                    )
                if discussion.id:
                    stamp_turn_index(discussion)
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
                # Triage's final act is to hand the discussion over to the
                # method it chose; a blocked handoff pauses for recovery.
                handoff = handle_triage_handoff(
                    discussion, db, get_state_fn)
                if handoff is not None:
                    return handoff
                return {
                    "method_complete": True,
                    "turn_number": discussion.turn_number,
                    "current_round": discussion.current_round,
                    "state": get_state_fn(),
                }

        # Persist method state on every completed turn — round-lifecycle
        # mutations (phase_round, huddle sub-state, revision counters)
        # must survive a crash/reload even without a phase transition
        # (issue #16).
        if discussion.id:
            stamp_turn_index(discussion)
            db.update_discussion(
                discussion.id,
                method_state=serialize_method_state(discussion.method_state),
            )

    # Check if max_rounds has been reached
    max_r = discussion.max_rounds
    if max_r > 0 and discussion.current_round > max_r:
        return {
            "max_rounds_reached": True,
            "turn_number": discussion.turn_number,
            "current_round": discussion.current_round,
            "state": get_state_fn(),
        }

    # Check if cost limit has been reached
    if discussion.cost_limit > 0:
        total_cost = calculate_discussion_cost(discussion)
        if total_cost >= discussion.cost_limit:
            return {
                "cost_limit_reached": True,
                "total_cost": total_cost,
                "cost_limit": discussion.cost_limit,
                "turn_number": discussion.turn_number,
                "current_round": discussion.current_round,
                "state": get_state_fn(),
            }

    # Recompute the speaker from live state: a phase transition above may
    # have reordered ``turn_order`` and reset ``current_turn_index`` to 0,
    # which would make the ``next_speaker`` captured from ``advance_turn()``
    # stale and point the frontend at the wrong participant.
    final_speaker = discussion.current_speaker
    return {
        "next_speaker": final_speaker.to_dict() if final_speaker else None,
        "turn_number": discussion.turn_number,
        "current_round": discussion.current_round,
        "state": get_state_fn(),
    }


def reassign_turn(moderator: Moderator, entity_id: int) -> dict:
    """Reassign the current turn to a different participant.

    Returns a dict with the reassigned entity, or an error dict on failure.
    The caller is responsible for appending state and sending notifications.
    """
    entity = moderator.reassign_turn(entity_id)
    if entity:
        return {"reassigned_to": entity.to_dict()}
    return {"error": "Could not reassign turn"}

