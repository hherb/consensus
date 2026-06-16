"""Active discussion operations — message submission, turn management, mediation, conclusion."""

import json
import logging
import re
import time
from typing import Callable, Optional

from .database import Database
from .methods import get_active_method, get_method, serialize_method_state
from .models import Discussion, Entity, EntityType, Message, MessageRole, StoryboardEntry
from .moderator import Moderator
from .pricing import PricingCache

logger = logging.getLogger(__name__)

# Matches the *entire* formatted pass message ("*Name passed this round.*"),
# anchored so the phrase appearing inside a longer real contribution does not
# count as a pass.
_FORMATTED_PASS_RE = re.compile(r"^.+ passed this round\.$")


def is_pass(content: str) -> bool:
    """Check if a participant's response is a pass (raw AI output or formatted).

    Recognises bracket notation ([PASS]), plain PASS, and the formatted
    '*Name passed this round.*' variant (which must be the whole message).
    """
    stripped = content.strip().strip("*_").strip()
    if stripped.upper() in ("[PASS]", "PASS"):
        return True
    # Match the formatted version only when it is the entire message, so a
    # participant mentioning the phrase mid-sentence is not misread as a pass.
    return bool(_FORMATTED_PASS_RE.match(stripped))


def calculate_discussion_cost(discussion: Discussion) -> float:
    """Sum the cost of all messages in the discussion."""
    return sum(m.cost or 0.0 for m in discussion.messages)


def submit_human_message(
    discussion: Discussion, db: Database, entity_id: int, content: str,
) -> dict:
    """Submit a message from a human participant.

    Returns a dict with the message data, or an error dict if the entity
    is not found or it is not their turn.
    """
    entity = discussion.get_entity(entity_id)
    if not entity:
        return {"error": "Entity not found"}

    current = discussion.current_speaker
    if not current or current.id != entity_id:
        return {"error": f"It's not {entity.name}'s turn"}

    msg = Message(
        entity_id=entity_id, entity_name=entity.name,
        content=content, role=MessageRole.PARTICIPANT,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, entity_id, content, "participant",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()


def submit_moderator_message(
    discussion: Discussion, db: Database, content: str,
) -> dict:
    """Submit a message from the human moderator.

    Returns a dict with the message data, or an error dict if no moderator
    is configured.
    """
    mod = discussion.moderator
    if not mod:
        return {"error": "No moderator"}

    msg = Message(
        entity_id=mod.id, entity_name=mod.name,
        content=content, role=MessageRole.MODERATOR,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, mod.id, content, "moderator",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()


async def _run_triage_recommender(
    discussion: Discussion, moderator_entity: Entity, key_resolver,
) -> None:
    """Call MethodRecommender after the triage moderator's synthesis turn."""
    from .ai_client import AIClient
    from .methods import list_methods
    from .methods.recommender import MethodRecommender

    state = discussion.method_state
    characterization = state.get("moderator_characterization", "")
    if not moderator_entity.ai_config:
        return

    api_key = key_resolver(
        moderator_entity.ai_config.provider_id,
        moderator_entity.ai_config.api_key_env,
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
    except Exception:
        logger.exception("Triage recommender call failed")
        state["recommendations"] = []
        state["recommended_method"] = "open_discussion"
    finally:
        await ai_client.close()


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

        # Detect if the participant chose to pass
        passed = is_pass(resp.content)

        # Method-specific response post-processing
        method = get_active_method(discussion)
        if method and not passed:
            processed = method.process_response(
                resp.content, current, discussion)
            content = processed.display_content
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
                await _run_triage_recommender(discussion, current, key_resolver)
                if discussion.id:
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
        else:
            content = resp.content

        if passed:
            content = f"*{current.name} passed this round.*"

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
            temperature_used=current.ai_config.temperature if current.ai_config else 0,
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
        logger.exception("AI generation failed for %s", current.name)
        # Post a visible notification so the moderator/participants
        # know this participant was skipped due to an API error.
        error_notice = (
            f"*{current.name} could not respond due to an API error "
            f"({type(e).__name__}). Skipping to the next participant.*"
        )
        msg = Message(
            entity_id=current.id, entity_name=current.name,
            content=error_notice, role=MessageRole.PARTICIPANT,
        )
        discussion.messages.append(msg)
        db.add_message(
            discussion.id, current.id, error_notice, "participant",
            turn_number=discussion.turn_number,
        )
        result = msg.to_dict()
        result["error"] = str(e)
        result["skipped"] = True
        return result


def switch_discussion_method(
    discussion: Discussion, db: Database, method_name: str,
) -> dict:
    """Switch the discussion to a new method (used by triage).

    Reinitializes method_state, persists to DB, and adds a system
    message announcing the transition. Returns the new method's
    metadata dict, or an error dict.
    """
    if method_name == "triage":
        return {"error": "Cannot switch to triage method"}

    try:
        method = get_method(method_name)
    except KeyError:
        return {"error": f"Unknown method: {method_name!r}"}

    discussion.discussion_method = method_name
    discussion.method_state = method.init_state(discussion)

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
            return {"error": f"Summary generation failed: {e}"}
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

    next_speaker = moderator.advance_turn()

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

        # Check for phase transition
        if method.should_advance_phase(discussion):
            new_phase = method.advance_phase(discussion)
            if new_phase:
                # Let the method reorder turns for the new phase
                new_order = method.get_turn_order(
                    list(discussion.turn_order), discussion)
                if new_order != list(discussion.turn_order):
                    discussion.turn_order = new_order
                    discussion.current_turn_index = 0

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
                # All phases exhausted
                if discussion.id:
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
                # Triage special case: switch to chosen method
                chosen = discussion.method_state.get("chosen_method")
                if (discussion.discussion_method == "triage"
                        and chosen):
                    switch_result = switch_discussion_method(
                        discussion, db, chosen)
                    if "error" not in switch_result:
                        # Reorder turns for the new method
                        new_method = get_active_method(discussion)
                        if new_method:
                            new_order = new_method.get_turn_order(
                                list(discussion.turn_order), discussion)
                            if new_order != list(discussion.turn_order):
                                discussion.turn_order = new_order
                                discussion.current_turn_index = 0
                        return {
                            "method_switched": True,
                            "new_method": switch_result,
                            "turn_number": discussion.turn_number,
                            "current_round": discussion.current_round,
                            "state": get_state_fn(),
                        }
                return {
                    "method_complete": True,
                    "turn_number": discussion.turn_number,
                    "current_round": discussion.current_round,
                    "state": get_state_fn(),
                }

            # Persist method state after phase transition
            if discussion.id:
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


async def mediate(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache, context: str = "",
) -> dict:
    """Have the moderator intervene to mediate a conflict.

    Returns a dict with the mediation message, or an error/awaiting dict.
    """
    mod = discussion.moderator
    if not mod:
        return {"error": "No moderator"}

    if mod.entity_type == EntityType.AI:
        try:
            resp = await moderator.mediate(context)
            cost = pricing.calculate_cost_with_refresh(
                resp.model,
                mod.ai_config.base_url if mod.ai_config else "",
                resp.prompt_tokens,
                resp.completion_tokens,
            )
            msg = Message(
                entity_id=mod.id, entity_name=mod.name,
                content=resp.content, role=MessageRole.MODERATOR,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                cost=cost,
            )
            discussion.messages.append(msg)
            prompt_id = moderator.prompt_id(
                "moderator", "ai", "mediate",
            )
            db.add_message(
                discussion.id, mod.id, resp.content, "moderator",
                turn_number=discussion.turn_number,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                prompt_id=prompt_id,
                cost=cost,
            )
            return msg.to_dict()
        except Exception as e:
            logger.exception("Mediation failed")
            return {"error": f"Mediation failed: {e}"}
    return {"awaiting_human_moderator": True}


async def conclude_discussion(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache,
) -> dict:
    """End the discussion, generating a final synthesis if the moderator is AI.

    Marks the discussion as concluded and persists the status change.
    Returns a result dict (the caller is responsible for appending state).
    """
    mod = discussion.moderator
    if mod and mod.entity_type == EntityType.AI:
        try:
            resp = await moderator.generate_conclusion()
            conclusion = resp.content
            cost = pricing.calculate_cost_with_refresh(
                resp.model,
                mod.ai_config.base_url if mod.ai_config else "",
                resp.prompt_tokens,
                resp.completion_tokens,
            )
            msg = Message(
                entity_id=mod.id, entity_name=mod.name,
                content=f"## Final Synthesis\n\n{conclusion}",
                role=MessageRole.MODERATOR,
                model_used=resp.model,
                cost=cost,
            )
            discussion.messages.append(msg)
            db.add_message(
                discussion.id, mod.id,
                f"## Final Synthesis\n\n{conclusion}", "moderator",
                turn_number=discussion.turn_number,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                cost=cost,
            )

            entry = StoryboardEntry(
                turn_number=discussion.turn_number,
                summary=f"CONCLUSION: {conclusion}",
                speaker_name=mod.name,
            )
            discussion.storyboard.append(entry)
            db.add_storyboard_entry(
                discussion.id, discussion.turn_number,
                f"CONCLUSION: {conclusion}", mod.id,
            )
        except Exception as e:
            logger.exception("Conclusion generation failed")
            # Continue to mark discussion as concluded even if AI fails

    discussion.is_active = False
    discussion.status = "concluded"
    if discussion.id:
        db.update_discussion(
            discussion.id,
            status="concluded", ended_at=time.time(),
        )
    return {"concluded": True}
