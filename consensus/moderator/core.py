"""The ``Moderator`` class: prompt lookup, AI clients and turn bookkeeping,
composed with the context, turn and synthesis mixins."""

import asyncio
import logging
from typing import Callable, Optional

from ..ai_client import AIClient
from ..database import Database
from ..models import ConfigurationError, Discussion, Entity
from ..tools import ToolRegistry
from .context import ContextMixin
from .synthesis import SynthesisMixin
from .turn import TurnMixin

logger = logging.getLogger(__name__)


class Moderator(ContextMixin, TurnMixin, SynthesisMixin):
    """Manages discussion flow, turn-taking, and synthesis."""

    def __init__(self, discussion: Discussion, db: Database,
                 key_resolver: Optional[Callable[[int, str], str]] = None,
                 tool_registry: Optional[ToolRegistry] = None,
                 progress_callback: Optional[Callable[[dict], None]] = None,
                 ) -> None:
        self.discussion = discussion
        self.db = db
        self._clients: dict[int, AIClient] = {}
        # Optional callback: (provider_id, env_var) -> api_key
        # When set, overrides the entity's resolved api_key for AI calls.
        self._key_resolver = key_resolver
        self._tool_registry = tool_registry
        # Optional callback receiving tool_progress event dicts so the
        # UI can show what a participant is doing mid-turn.
        self._progress_callback = progress_callback

    def _emit_progress(self, data: dict) -> None:
        """Report tool-loop progress to the UI, never breaking the turn.

        A UI push failure (e.g. a torn-down webview) must not abort an
        otherwise healthy generation, so callback errors are logged and
        swallowed.
        """
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(data)
        except Exception:
            logger.warning("Tool progress callback failed", exc_info=True)

    def resolve_prompt(self, role: str, target: str, task: str,
                       **variables: object) -> str:
        """Look up a prompt template from DB and fill in template variables."""
        row = self.db.get_prompt_by_task(role, target, task)
        if not row:
            return ""
        template: str = row["content"]
        for key, val in variables.items():
            template = template.replace("{" + key + "}", str(val))
        return template

    def prompt_id(self, role: str, target: str, task: str) -> int:
        """Get prompt ID for metadata logging."""
        row = self.db.get_prompt_by_task(role, target, task)
        return row["id"] if row else 0

    def _resolve_api_key(self, entity: Entity) -> str:
        """Resolve the API key for an entity, using BYOK resolver if available."""
        if not entity.ai_config:
            return ""
        if self._key_resolver:
            return self._key_resolver(
                entity.ai_config.provider_id,
                "",  # env_var looked up by resolver
            )
        return entity.ai_config.api_key

    def _get_client(self, entity: Entity) -> AIClient:
        """Return a cached AI client for the given entity, creating one if needed."""
        if not entity.ai_config:
            raise ConfigurationError(
                f"{entity.name} has no AI configuration — assign a provider "
                f"and model to this entity in the Profiles tab.",
            )
        api_key = self._resolve_api_key(entity)
        # Recreate client if the API key has changed (e.g. BYOK per-request)
        existing = self._clients.get(entity.id)
        if existing and existing.api_key != api_key:
            # Key changed — schedule async close and create new one
            old_client = self._clients.pop(entity.id)
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(old_client.close())

                def _on_close_done(t: asyncio.Task) -> None:  # type: ignore[type-arg]
                    exc = t.exception()
                    if exc:
                        logger.debug("Error closing old AI client: %s", exc)

                task.add_done_callback(_on_close_done)
            except RuntimeError:
                pass  # No event loop; client will be garbage-collected
        if entity.id not in self._clients:
            self._clients[entity.id] = AIClient(
                base_url=entity.ai_config.base_url,
                api_key=api_key,
            )
        return self._clients[entity.id]

    async def close(self) -> None:
        """Close all cached AI clients."""
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing AI client", exc_info=True)
        self._clients.clear()

    def get_human_guidance(self, role: str) -> str:
        """Get guidance text to display to a human moderator or participant."""
        return self.resolve_prompt(
            role, "human", "guidance",
            topic=self.discussion.topic,
            participants=self._participant_names(),
        )

    def peek_next_speaker(self) -> Optional[Entity]:
        """Return the next speaker without advancing the turn index."""
        if not self.discussion.turn_order:
            return None
        next_idx = (
            (self.discussion.current_turn_index + 1)
            % len(self.discussion.turn_order)
        )
        next_id = self.discussion.turn_order[next_idx]
        return self.discussion.get_entity(next_id)

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

    def reassign_turn(self, entity_id: int) -> Optional[Entity]:
        """Reassign the current turn to a specific entity."""
        entity = self.discussion.get_entity(entity_id)
        if entity and entity_id in self.discussion.turn_order:
            self.discussion.current_turn_index = (
                self.discussion.turn_order.index(entity_id)
            )
            return entity
        return None
