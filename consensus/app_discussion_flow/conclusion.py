"""Moderator interventions that end or steady a discussion.

``mediate`` lets the moderator step in mid-discussion; ``conclude_discussion``
generates the final synthesis (when the moderator is an AI) and marks the
discussion concluded.  Both are terminal-ish moderator actions with no
dependency on the turn-rotation machinery, so they sit apart from
``turns``.
"""

import logging
import time

from ..database import Database
from ..models import (
    Discussion, EntityType, Message, MessageRole, StoryboardEntry,
)
from ..moderator import Moderator
from ..pricing import PricingCache
from .helpers import describe_flow_error, post_notice

logger = logging.getLogger(__name__)

#: Transcript notice posted when the final synthesis could not be produced
#: at all.  The discussion still concludes — but never silently (#71).
_CONCLUSION_FAILURE_NOTICE = (
    "**The Final Synthesis could not be generated.** {detail}\n\n"
    "The discussion has been marked as concluded; you can reopen it and "
    "conclude again once the cause is resolved."
)

#: Transcript notice for the other half of the failure window: the
#: synthesis above was generated and is on screen, but recording it
#: failed.  Telling the user it "could not be generated" while it sits
#: directly above the notice is worse than saying nothing (#71 follow-up).
_CONCLUSION_NOT_SAVED_NOTICE = (
    "**The Final Synthesis was generated but could not be fully saved.** "
    "{detail}\n\nIt is shown above, but it may be missing or incomplete "
    "when this discussion is reloaded — copy it now if you need it."
)

#: Transcript notice posted when a requested mediation never happened.
#: A toast alone is not a record: it is gone in seconds, and the user is
#: left with a discussion that shows no trace of the intervention they
#: asked for (golden rule 6, #71 follow-up).
_MEDIATION_FAILURE_NOTICE = (
    "**The moderator's mediation could not be generated.** {detail}\n\n"
    "The discussion is unchanged — you can request mediation again once "
    "the cause is resolved."
)


async def mediate(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache, context: str = "",
) -> dict:
    """Have the moderator intervene to mediate a conflict.

    Returns a dict with the mediation message, or an error/awaiting dict.
    A failure is also posted into the transcript, so the attempt leaves a
    durable trace rather than only a toast (golden rule 6).
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
            # describe_flow_error, not str(e): for an HTTP error the
            # provider's response body is where the actionable message
            # lives, and a bug here must not read as a provider fault
            # (issues #71, #74).
            detail = describe_flow_error(e)
            post_notice(
                discussion, db, mod,
                _MEDIATION_FAILURE_NOTICE.format(detail=detail),
            )
            return {"error": f"Mediation failed: {detail}"}
    return {"awaiting_human_moderator": True}


async def conclude_discussion(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache,
) -> dict:
    """End the discussion, generating a final synthesis if the moderator is AI.

    Marks the discussion as concluded and persists the status change.
    Returns a result dict (the caller is responsible for appending state)
    carrying ``conclusion_error`` when the synthesis could not be produced
    *or* could not be recorded; the transcript notice distinguishes the
    two, since a synthesis that is on screen but unsaved needs different
    advice from one that never existed.
    """
    conclusion_error = ""
    mod = discussion.moderator
    if mod and mod.entity_type == EntityType.AI:
        # Flipped once the synthesis is in ``discussion.messages`` and so
        # already visible to the user; everything that can fail after that
        # point is a persistence failure, not a generation failure.
        synthesis_shown = False
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
            synthesis_shown = True
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
            # The discussion still concludes — an expensive session must
            # not be left half-ended — but the user has to be told why
            # (golden rule 6, issue #71).  The block spans generation and
            # two persistence steps, so which of the two notices is true
            # depends on how far it got: the synthesis is appended to the
            # transcript before it is written, and a write failure after
            # that leaves it on screen but unrecorded.
            conclusion_error = describe_flow_error(e)
            template = (_CONCLUSION_NOT_SAVED_NOTICE if synthesis_shown
                        else _CONCLUSION_FAILURE_NOTICE)
            post_notice(
                discussion, db, mod,
                template.format(detail=conclusion_error),
            )

    discussion.is_active = False
    discussion.status = "concluded"
    if discussion.id:
        db.update_discussion(
            discussion.id,
            status="concluded", ended_at=time.time(),
        )
    result: dict = {"concluded": True}
    if conclusion_error:
        result["conclusion_error"] = conclusion_error
    return result
