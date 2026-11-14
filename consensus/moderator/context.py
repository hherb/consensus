"""Context assembly: per-participant strategy, history loading and the
OpenAI message array each AI call is given."""

from typing import Optional

from ..context_strategies import (
    ContextConfig, ContextStrategy,
    load_context_messages, load_context_messages_async,
)
from ..methods import get_active_method
from ..models import EntityType
from ..tools_image import build_image_content_blocks


class ContextMixin:
    """Builds the message context for participant and moderator calls."""

    def _participant_names(self) -> str:
        """Return a formatted comma-separated list of participant names and types."""
        return ", ".join(
            f"{e.name} ({'AI' if e.entity_type == EntityType.AI else 'Human'})"
            for e in self.discussion.entities
        )

    def _get_context_config(self, entity_id: Optional[int]) -> ContextConfig:
        """Look up the context strategy for a participant.

        Checks the ``discussion_members`` row first, falls back to the
        discussion-level default (read from the in-memory Discussion
        object to avoid an extra DB query), then to the hard-coded
        default (sliding_window of 20).

        For TOKEN_WINDOW strategy, populates model_context_length and
        reserved_output_tokens from the entity's AI config and pricing cache.
        """
        disc_defaults = {
            "default_context_strategy": self.discussion.default_context_strategy,
            "default_context_window_size": self.discussion.default_context_window_size,
        }
        if entity_id and self.discussion.id:
            member = self.db.get_discussion_member(self.discussion.id, entity_id)
            if member:
                config = ContextConfig.from_member_row(member, disc_defaults)
            else:
                config = ContextConfig()
        else:
            config = ContextConfig()

        # Populate token-aware fields when TOKEN_WINDOW is selected
        if config.strategy == ContextStrategy.TOKEN_WINDOW and entity_id:
            entity = next(
                (e for e in self.discussion.entities if e.id == entity_id),
                None,
            )
            if entity and entity.ai_config and self.db.pricing:
                config.model_context_length = self.db.pricing.get_context_length(
                    entity.ai_config.model, entity.ai_config.base_url)
                config.reserved_output_tokens = entity.ai_config.max_tokens

        return config

    async def _load_context_messages(self, entity_id: Optional[int],
                                      config: Optional[ContextConfig] = None) -> list:
        """Load messages from DB using the entity's context strategy."""
        if config is None:
            config = self._get_context_config(entity_id)
        if config.strategy == ContextStrategy.SEMANTIC:
            from ..tools_memory import EmbeddingClient
            embed_client = EmbeddingClient(self.db)
            return await load_context_messages_async(
                self.db, self.discussion.id, config, embed_client)
        return load_context_messages(self.db, self.discussion.id, config)

    def _format_messages(
        self, raw_messages: list, system_prompt: str, task: str,
        current_entity_id: Optional[int] = None,
        images: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Format Message objects into an OpenAI message array.

        Uses role-based formatting: messages from the current entity become
        'assistant' role, all others become 'user' role with speaker prefix.

        If a discussion method provides a ``filter_context_message`` hook,
        each message is passed through it (e.g. for Delphi anonymisation).
        """
        method = get_active_method(self.discussion)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        messages.append({
            "role": "user",
            "content": f"Discussion topic: {self.discussion.topic}",
        })

        # Inject discussion images as a multimodal message
        if images:
            content_blocks = build_image_content_blocks(images)
            if content_blocks:
                messages.append({"role": "user", "content": content_blocks})

        for msg in raw_messages:
            if current_entity_id and msg.entity_id == current_entity_id:
                role = "assistant"
                content = msg.content
            else:
                role = "user"
                content = f"[{msg.entity_name}]: {msg.content}"

            if method:
                content = method.filter_context_message(
                    msg.entity_name, content, role, self.discussion,
                    current_entity_id=current_entity_id)

            # Skip empty messages entirely: filters suppress private content
            # by returning "", and some APIs (e.g. Kimi) reject empty content.
            if not content:
                continue

            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": task})
        return messages

    def _enrich_token_window_config(self, config: ContextConfig,
                                     system_prompt: str, task: str) -> None:
        """Pre-compute system overhead tokens for TOKEN_WINDOW strategy.

        Estimates tokens consumed by system prompt, topic, and task
        instruction so the context loader can calculate the remaining
        budget for discussion messages.  Image blocks are not counted
        here — the 200-token safety margin in _load_token_window covers
        that gap.
        """
        if config.strategy != ContextStrategy.TOKEN_WINDOW:
            return
        from ..token_utils import estimate_tokens
        config.system_tokens = (
            estimate_tokens(system_prompt) + 4
            + estimate_tokens(f"Discussion topic: {self.discussion.topic}") + 4
            + estimate_tokens(task) + 4
            + 3  # priming
        )

    async def _build_context(self, system_prompt: str, task: str,
                              current_entity_id: Optional[int] = None) -> list[dict]:
        """Build an OpenAI message array from DB-loaded discussion history.

        Loads context using the participant's configured strategy
        (full / sliding_window / summary / semantic / token_window),
        then formats into role-based messages.
        """
        config = self._get_context_config(current_entity_id)
        self._enrich_token_window_config(config, system_prompt, task)

        raw = await self._load_context_messages(current_entity_id, config)
        return self._format_messages(raw, system_prompt, task,
                                     current_entity_id)

    async def _build_multimodal_context(
        self, system_prompt: str, task: str,
        current_entity_id: Optional[int] = None,
        images: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Build context with images as multimodal content blocks.

        Same as ``_build_context`` but injects discussion images as
        ``image_url`` content blocks for vision-capable models.
        """
        config = self._get_context_config(current_entity_id)
        self._enrich_token_window_config(config, system_prompt, task)

        raw = await self._load_context_messages(current_entity_id, config)
        return self._format_messages(raw, system_prompt, task,
                                     current_entity_id, images=images)
