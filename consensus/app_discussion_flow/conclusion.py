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

logger = logging.getLogger(__name__)


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
