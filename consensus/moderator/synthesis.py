"""The moderator's own AI calls: per-turn summary, final conclusion and
mediation."""

from ..ai_client import AIResponse
from ..methods import get_active_method
from ..models import EntityType
from .constants import (
    CONCLUSION_MAX_TOKENS, MEDIATION_MAX_TOKENS, MODERATOR_TEMPERATURE,
    SUMMARY_MAX_TOKENS,
)


class SynthesisMixin:
    """Summary, conclusion and mediation calls made as the moderator."""

    async def generate_summary(self,
                               next_speaker_name: str = "") -> AIResponse:
        """Generate a moderator summary after a participant's turn."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        last_msg = self.discussion.messages[-1] if self.discussion.messages else None
        speaker = last_msg.entity_name if last_msg else "Unknown"
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        # Check for method-specific summary prompt
        method = get_active_method(self.discussion)
        method_summary = (
            method.get_summary_prompt(self.discussion, speaker,
                                      next_speaker_name)
            if method else ""
        )

        if method_summary:
            task = method_summary
        else:
            task = self.resolve_prompt(
                "moderator", "ai", "summarize",
                entity_name=mod.name,
                topic=self.discussion.topic,
                speaker_name=speaker,
                turn_number=str(self.discussion.turn_number),
                participants=participants,
                next_speaker_name=next_speaker_name,
            )
        if not task:
            task = f"Summarize turn {self.discussion.turn_number} by {speaker}."

        return await client.complete(
            messages=await self._build_context(system_prompt, task,
                                               current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=SUMMARY_MAX_TOKENS,
        )

    async def generate_conclusion(self) -> AIResponse:
        """Generate a final synthesis/conclusion for the discussion."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        # Check for method-specific conclusion prompt
        method = get_active_method(self.discussion)
        method_conclusion = (
            method.get_conclusion_prompt(self.discussion)
            if method else ""
        )

        if method_conclusion:
            task = method_conclusion
        else:
            task = self.resolve_prompt(
                "moderator", "ai", "conclude",
                topic=self.discussion.topic,
                participants=participants,
            )
        if not task:
            task = f"Conclude the discussion on '{self.discussion.topic}'."

        return await client.complete(
            messages=await self._build_context(system_prompt, task,
                                               current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=CONCLUSION_MAX_TOKENS,
        )

    async def mediate(self, context: str = "") -> AIResponse:
        """Have the moderator intervene to mediate a conflict."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        task = self.resolve_prompt(
            "moderator", "ai", "mediate",
            context=context or "A disagreement has arisen.",
            topic=self.discussion.topic,
            participants=participants,
        )
        if not task:
            task = f"Mediate: {context or 'A disagreement has arisen.'}"

        return await client.complete(
            messages=await self._build_context(system_prompt, task,
                                               current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=MEDIATION_MAX_TOKENS,
        )
