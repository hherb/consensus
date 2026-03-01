"""Moderator logic for managing discussions."""

from typing import Optional

from .models import Discussion, Entity, EntityType, Message, MessageRole, StoryboardEntry
from .ai_client import AIClient


class Moderator:
    """Manages discussion flow, turn-taking, and synthesis."""

    def __init__(self, discussion: Discussion):
        self.discussion = discussion

    def _build_context(self, system_prompt: str, task: str) -> list[dict]:
        """Build message list for AI context."""
        messages = [{"role": "system", "content": system_prompt}]

        context = f"Discussion topic: {self.discussion.topic}\n\n"
        context += "Participants: " + ", ".join(
            f"{e.name} ({'AI' if e.entity_type == EntityType.AI else 'Human'})"
            for e in self.discussion.entities
        ) + "\n\n"

        for msg in self.discussion.messages[-20:]:
            context += f"[{msg.entity_name}]: {msg.content}\n\n"

        messages.append({"role": "user", "content": context + "\n" + task})
        return messages

    def _get_client(self, entity: Entity) -> AIClient:
        if not entity.ai_config:
            raise ValueError(f"{entity.name} has no AI configuration")
        return AIClient(
            base_url=entity.ai_config.base_url,
            api_key=entity.ai_config.api_key,
        )

    async def generate_turn(self, entity: Entity) -> str:
        """Generate an AI entity's contribution."""
        client = self._get_client(entity)
        cfg = entity.ai_config

        system_prompt = cfg.system_prompt or (
            f"You are {entity.name}, a participant in a moderated discussion. "
            f"The topic is: {self.discussion.topic}. "
            f"Contribute thoughtfully and constructively. Be concise but substantive. "
            f"Address points raised by other participants when relevant."
        )

        task = (
            f"It is your turn to speak as {entity.name}. "
            f"Provide your contribution to the discussion. "
            f"Be concise (2-4 paragraphs max). Respond only with your contribution."
        )

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )

    async def generate_summary(self) -> str:
        """Generate a moderator summary after a turn."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return ""

        client = self._get_client(mod)
        last_msg = self.discussion.messages[-1] if self.discussion.messages else None
        speaker = last_msg.entity_name if last_msg else "Unknown"

        system_prompt = (
            f"You are {mod.name}, the moderator of a structured discussion. "
            f"Summarize key points after each turn. Identify agreement and disagreement. "
            f"Synthesize emerging consensus or highlight tensions. Be neutral and concise."
        )

        task = (
            f"Turn {self.discussion.turn_number}: {speaker} just spoke. "
            f"Provide a brief synthesis (2-3 sentences) of the key point(s) made "
            f"and how they relate to the overall discussion so far."
        )

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=mod.ai_config.model,
            temperature=0.5,
            max_tokens=512,
        )

    async def generate_conclusion(self) -> str:
        """Generate a final synthesis/conclusion."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return ""

        client = self._get_client(mod)

        system_prompt = (
            f"You are {mod.name}, the moderator. "
            f"The discussion is concluding. Provide a comprehensive final synthesis."
        )

        task = (
            f"The discussion on '{self.discussion.topic}' is concluding. "
            f"Provide a final synthesis that:\n"
            f"1) Summarizes the main positions expressed\n"
            f"2) Identifies areas of consensus\n"
            f"3) Notes remaining points of disagreement\n"
            f"4) Offers a balanced conclusion\n"
            f"Be thorough but concise (3-5 paragraphs)."
        )

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=mod.ai_config.model,
            temperature=0.5,
            max_tokens=1024,
        )

    async def mediate(self, context: str = "") -> str:
        """Moderator intervenes to mediate a conflict."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return ""

        client = self._get_client(mod)

        system_prompt = (
            f"You are {mod.name}, the moderator. A conflict has arisen. "
            f"Acknowledge both perspectives fairly, identify common ground, "
            f"and suggest a constructive path forward. Be diplomatic and balanced."
        )

        task = f"Please mediate: {context or 'A disagreement has arisen. Help find common ground.'}"

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=mod.ai_config.model,
            temperature=0.5,
            max_tokens=512,
        )

    def advance_turn(self) -> Optional[Entity]:
        """Advance to the next turn in the rotation."""
        if not self.discussion.turn_order:
            return None
        self.discussion.current_turn_index = (
            (self.discussion.current_turn_index + 1) % len(self.discussion.turn_order)
        )
        self.discussion.turn_number += 1
        return self.discussion.current_speaker

    def reassign_turn(self, entity_id: str) -> Optional[Entity]:
        """Moderator reassigns the current turn to a specific entity."""
        entity = self.discussion.get_entity(entity_id)
        if entity and entity_id in self.discussion.turn_order:
            self.discussion.current_turn_index = self.discussion.turn_order.index(entity_id)
            return entity
        return None
