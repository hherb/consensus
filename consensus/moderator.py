"""Moderator logic for managing discussions."""

from typing import Optional

from .models import Discussion, Entity, EntityType
from .ai_client import AIClient, AIResponse
from .database import Database

# How many recent messages to include in AI context
CONTEXT_MESSAGE_LIMIT = 20

# Moderator AI generation settings
MODERATOR_TEMPERATURE = 0.5
SUMMARY_MAX_TOKENS = 512
CONCLUSION_MAX_TOKENS = 1024
MEDIATION_MAX_TOKENS = 512


class Moderator:
    """Manages discussion flow, turn-taking, and synthesis."""

    def __init__(self, discussion: Discussion, db: Database) -> None:
        self.discussion = discussion
        self.db = db

    def _resolve_prompt(self, role: str, target: str, task: str,
                        **variables: object) -> str:
        """Look up a prompt template from DB and fill in template variables."""
        row = self.db.get_prompt_by_task(role, target, task)
        if not row:
            return ""
        template: str = row["content"]
        for key, val in variables.items():
            template = template.replace("{" + key + "}", str(val))
        return template

    def _prompt_id(self, role: str, target: str, task: str) -> str:
        """Get prompt ID for metadata logging."""
        row = self.db.get_prompt_by_task(role, target, task)
        return row["id"] if row else ""

    def _participant_names(self) -> str:
        """Return a formatted comma-separated list of participant names and types."""
        return ", ".join(
            f"{e.name} ({'AI' if e.entity_type == EntityType.AI else 'Human'})"
            for e in self.discussion.entities
        )

    def _build_context(self, system_prompt: str, task: str) -> list[dict]:
        """Build the message list for AI context from discussion history."""
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        context = f"Discussion topic: {self.discussion.topic}\n\n"
        for msg in self.discussion.messages[-CONTEXT_MESSAGE_LIMIT:]:
            context += f"[{msg.entity_name}]: {msg.content}\n\n"

        messages.append({"role": "user", "content": context + "\n" + task})
        return messages

    def _get_client(self, entity: Entity) -> AIClient:
        """Create an AI client for the given entity's provider configuration."""
        if not entity.ai_config:
            raise ValueError(f"{entity.name} has no AI configuration")
        return AIClient(
            base_url=entity.ai_config.base_url,
            api_key=entity.ai_config.api_key,
        )

    async def generate_turn(self, entity: Entity) -> AIResponse:
        """Generate an AI entity's contribution to the discussion."""
        client = self._get_client(entity)
        cfg = entity.ai_config
        participants = self._participant_names()

        if cfg.system_prompt:
            system_prompt = cfg.system_prompt
        else:
            system_prompt = self._resolve_prompt(
                "participant", "ai", "system",
                entity_name=entity.name,
                topic=self.discussion.topic,
                participants=participants,
            )

        task = self._resolve_prompt(
            "participant", "ai", "turn",
            entity_name=entity.name,
            topic=self.discussion.topic,
            participants=participants,
        )
        if not task:
            task = f"It is your turn to speak as {entity.name}. Be concise."

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )

    async def generate_summary(self) -> AIResponse:
        """Generate a moderator summary after a participant's turn."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        last_msg = self.discussion.messages[-1] if self.discussion.messages else None
        speaker = last_msg.entity_name if last_msg else "Unknown"
        participants = self._participant_names()

        system_prompt = self._resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        task = self._resolve_prompt(
            "moderator", "ai", "summarize",
            entity_name=mod.name,
            topic=self.discussion.topic,
            speaker_name=speaker,
            turn_number=str(self.discussion.turn_number),
            participants=participants,
        )
        if not task:
            task = f"Summarize turn {self.discussion.turn_number} by {speaker}."

        return await client.complete(
            messages=self._build_context(system_prompt, task),
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

        system_prompt = self._resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        task = self._resolve_prompt(
            "moderator", "ai", "conclude",
            topic=self.discussion.topic,
            participants=participants,
        )
        if not task:
            task = f"Conclude the discussion on '{self.discussion.topic}'."

        return await client.complete(
            messages=self._build_context(system_prompt, task),
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

        system_prompt = self._resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        task = self._resolve_prompt(
            "moderator", "ai", "mediate",
            context=context or "A disagreement has arisen.",
            topic=self.discussion.topic,
            participants=participants,
        )
        if not task:
            task = f"Mediate: {context or 'A disagreement has arisen.'}"

        return await client.complete(
            messages=self._build_context(system_prompt, task),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=MEDIATION_MAX_TOKENS,
        )

    def get_human_guidance(self, role: str) -> str:
        """Get guidance text to display to a human moderator or participant."""
        return self._resolve_prompt(
            role, "human", "guidance",
            topic=self.discussion.topic,
            participants=self._participant_names(),
        )

    def advance_turn(self) -> Optional[Entity]:
        """Advance to the next speaker in the turn order."""
        if not self.discussion.turn_order:
            return None
        self.discussion.current_turn_index = (
            (self.discussion.current_turn_index + 1)
            % len(self.discussion.turn_order)
        )
        self.discussion.turn_number += 1
        return self.discussion.current_speaker

    def reassign_turn(self, entity_id: str) -> Optional[Entity]:
        """Reassign the current turn to a specific entity."""
        entity = self.discussion.get_entity(entity_id)
        if entity and entity_id in self.discussion.turn_order:
            self.discussion.current_turn_index = (
                self.discussion.turn_order.index(entity_id)
            )
            return entity
        return None
