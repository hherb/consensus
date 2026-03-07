"""Application state and API for the discussion system."""

import contextvars
import json
import logging
import time
from typing import Optional, Callable

from .ai_client import AIClient
from .models import (
    Discussion, Entity, EntityType, Message, MessageRole, StoryboardEntry,
    resolve_api_key,
)
from .moderator import Moderator
from .database import Database
from .config import get_db_path, save_api_key, remove_api_key, has_api_key
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


def _is_pass(content: str) -> bool:
    """Check if a participant's response is a pass (raw AI output or formatted)."""
    stripped = content.strip().strip("*_").strip()
    if stripped.upper() in ("[PASS]", "PASS"):
        return True
    # Also match the formatted version: *Name passed this round.*
    return content.strip().endswith("passed this round.*")

# Per-request BYOK API keys, isolated via contextvars (no cross-request leakage)
_request_api_keys_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "_request_api_keys_var", default={},
)


class ConsensusApp:
    """Main application controller backed by SQLite."""

    def __init__(self, db_path: str = "") -> None:
        self.db = Database(db_path or get_db_path())
        self.db.purge_deleted_discussions()
        self.discussion = Discussion()
        self.tool_registry = ToolRegistry(db=self.db)
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
        )
        self._on_update: Optional[Callable] = None
        self.memory_available = False
        self._init_builtin_tools()
        self._init_memory_tools()

    def _init_builtin_tools(self) -> None:
        """Register built-in tool providers."""
        try:
            from .tools_builtin import create_web_search_provider
            provider = create_web_search_provider()
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("builtin", "python")
        except ImportError:
            logger.debug("Built-in tools not available")

    def _init_memory_tools(self) -> None:
        """Register institutional memory tool provider (requires [memory] extras)."""
        try:
            import sqlite_vec  # noqa: F401
            from .tools_memory import create_memory_provider
            provider = create_memory_provider(self.db)
            self.tool_registry.register_provider(provider)
            self.db.add_tool_provider("memory", "python")
            self.memory_available = True
            logger.debug("Institutional memory tools registered")
        except ImportError:
            logger.debug("Memory tools not available (install consensus[memory])")

    @staticmethod
    def set_request_api_keys(keys: dict[str, str]) -> None:
        """Set per-request API keys (BYOK) via contextvars. Called by the web server."""
        _request_api_keys_var.set(keys)

    @staticmethod
    def clear_request_api_keys() -> None:
        """Clear per-request API keys after the request is handled."""
        _request_api_keys_var.set({})

    def _resolve_key_for_moderator(self, provider_id: int,
                                   env_var: str) -> str:
        """Key resolver callback for the Moderator's AI clients."""
        # Look up env_var from DB if not provided
        if not env_var and provider_id:
            provider = self.db.get_provider(provider_id)
            if provider:
                env_var = provider.get("api_key_env") or ""
        return self.resolve_provider_api_key(provider_id, env_var)

    def resolve_provider_api_key(self, provider_id: int,
                                 env_var: str) -> str:
        """Resolve API key for a provider: BYOK first, then env var.

        Order of precedence:
        1. Per-request BYOK key (from browser)
        2. Environment variable (from server config / .env file)
        """
        # Check BYOK keys first (from contextvars, request-scoped)
        byok_key = _request_api_keys_var.get({}).get(str(provider_id), "")
        if byok_key:
            return byok_key
        # Fall back to env-based resolution (original behavior)
        return resolve_api_key(env_var or "")

    def set_update_callback(self, callback: Callable) -> None:
        """Register a callback invoked whenever state changes."""
        self._on_update = callback

    def _notify(self) -> None:
        """Push current state to the registered update callback."""
        if self._on_update:
            self._on_update(self.get_state())

    # ------------------------------------------------------------------
    # State for the frontend
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return the complete application state for the frontend."""
        state = self.discussion.to_dict()
        state["providers"] = self.get_providers()
        state["saved_entities"] = self.db.get_entities()
        state["prompts"] = self.db.get_prompts()
        state["discussions_history"] = self.db.get_discussions()
        state["tool_providers"] = self.db.get_tool_providers()
        return state

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def _provider_for_frontend(self, p: Optional[dict]) -> Optional[dict]:
        """Redact secrets before sending provider data to the frontend."""
        if not p:
            return None
        p = dict(p)
        env_var = p.get("api_key_env") or ""
        provider_id = p.get("id", 0)
        # Key is available if set via env OR via BYOK
        has_env = has_api_key(env_var)
        has_byok = bool(_request_api_keys_var.get({}).get(str(provider_id), ""))
        p["has_key"] = has_env or has_byok
        p.pop("api_key_env", None)
        return p

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "",
                     api_key: str = "") -> Optional[dict]:
        """Add a new API provider and return its data.

        If *api_key* is provided, save it to ~/.consensus/.env and store
        only the env var name in the database.
        """
        if api_key and api_key_env:
            save_api_key(api_key_env, api_key)
        pid = self.db.add_provider(name, base_url, api_key_env)
        return self._provider_for_frontend(self.db.get_provider(pid))

    def update_provider(self, provider_id: int,
                        api_key: str = "", **kwargs: object) -> bool:
        """Update an existing provider's fields.

        If *api_key* is provided (non-empty string), save it.
        If *api_key* is the sentinel "__REMOVE__", delete the stored key.
        """
        provider = self.db.get_provider(provider_id)
        if not provider:
            return False
        env_var = kwargs.get("api_key_env") or provider["api_key_env"]
        if api_key == "__REMOVE__" and env_var:
            remove_api_key(env_var)
        elif api_key and env_var:
            save_api_key(env_var, api_key)
        self.db.update_provider(provider_id, **kwargs)
        return True

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider by ID."""
        self.db.delete_provider(provider_id)
        return True

    def get_providers(self) -> list[dict]:
        """Return all configured providers (keys redacted)."""
        return [self._provider_for_frontend(p)
                for p in self.db.get_providers()]

    async def fetch_models(self, provider_id: int) -> list[str]:
        """Fetch available models from a provider's API."""
        provider = self.db.get_provider(provider_id)
        if not provider:
            return []
        api_key = self.resolve_provider_api_key(
            provider_id, provider["api_key_env"] or "",
        )
        async with AIClient(provider["base_url"], api_key) as client:
            return await client.list_models()

    # ------------------------------------------------------------------
    # Entity profile management (persistent)
    # ------------------------------------------------------------------

    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: int = 0, model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "",
                    entity_id: int = 0) -> Optional[dict]:
        """Create or update a persistent entity profile."""
        if entity_id:
            self.db.update_entity(
                entity_id, name=name, entity_type=entity_type,
                avatar_color=avatar_color, provider_id=provider_id,
                model=model, temperature=temperature,
                max_tokens=max_tokens, system_prompt=system_prompt,
            )
        else:
            entity_id = self.db.add_entity(
                name, entity_type, avatar_color, provider_id,
                model, temperature, max_tokens, system_prompt,
            )
        return self.db.get_entity(entity_id)

    def delete_entity(self, entity_id: int) -> dict:
        """Delete or deactivate an entity profile by ID."""
        return self.db.delete_entity(entity_id)

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a previously deactivated entity profile."""
        return self.db.reactivate_entity(entity_id)

    def get_entities(self) -> list[dict]:
        """Return all saved active entity profiles."""
        return self.db.get_entities()

    def get_inactive_entities(self) -> list[dict]:
        """Return all inactive (soft-deleted) entity profiles."""
        return self.db.get_inactive_entities()

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    def save_prompt(self, prompt_id: int, name: str, role: str,
                    target: str, task: str, content: str) -> Optional[dict]:
        """Create or update a prompt template."""
        pid = self.db.save_prompt(
            prompt_id or None, name, role, target, task, content,
        )
        return self.db.get_prompt(pid)

    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt template by ID."""
        self.db.delete_prompt(prompt_id)
        return True

    def get_prompts(self) -> list[dict]:
        """Return all prompt templates."""
        return self.db.get_prompts()

    # ------------------------------------------------------------------
    # Discussion setup
    # ------------------------------------------------------------------

    def add_to_discussion(self, entity_id: int,
                          is_moderator: bool = False,
                          also_participant: bool = False) -> dict:
        """Add a saved entity to the current discussion."""
        row = self.db.get_entity(entity_id)
        if not row:
            return {"error": "Entity not found"}

        entity = Entity.from_db_row(row)

        if self.discussion.get_entity(entity_id):
            return {"error": f"{entity.name} is already in the discussion"}

        self.discussion.entities.append(entity)

        if is_moderator:
            self.discussion.moderator_id = entity_id

        # Persist to DB if discussion is already started
        if self.discussion.id and self.discussion.status in ("active", "paused"):
            next_pos = len(self.discussion.turn_order)
            self.db.add_discussion_member(
                self.discussion.id, entity_id,
                is_moderator=is_moderator,
                also_participant=True,
                turn_position=next_pos,
            )
            self.discussion.turn_order.append(entity_id)

            sys_msg = Message(
                entity_id=entity_id, entity_name=entity.name,
                content=f"-- {entity.name} joined the discussion --",
                role=MessageRole.SYSTEM,
            )
            self.discussion.messages.append(sys_msg)
            self.db.add_message(
                self.discussion.id, entity_id,
                f"-- {entity.name} joined the discussion --",
                "system", turn_number=self.discussion.turn_number,
            )

        self._notify()
        return entity.to_dict()

    def remove_from_discussion(self, entity_id: int) -> dict | bool:
        """Remove an entity from the current discussion."""
        # Guard: cannot remove moderator or current speaker mid-discussion
        if self.discussion.id and self.discussion.status in ("active", "paused"):
            if entity_id == self.discussion.moderator_id:
                return {"error": "Cannot remove the moderator"}
            current = self.discussion.current_speaker
            if (self.discussion.status == "active"
                    and current and current.id == entity_id):
                return {"error": "Cannot remove the current speaker"}

        entity = self.discussion.get_entity(entity_id)
        entity_name = entity.name if entity else str(entity_id)

        # Adjust current_turn_index before removing from turn_order
        if entity_id in self.discussion.turn_order:
            removed_pos = self.discussion.turn_order.index(entity_id)
            self.discussion.turn_order.remove(entity_id)
            if removed_pos < self.discussion.current_turn_index:
                self.discussion.current_turn_index -= 1
            if self.discussion.turn_order:
                self.discussion.current_turn_index = (
                    self.discussion.current_turn_index
                    % len(self.discussion.turn_order)
                )
            else:
                self.discussion.current_turn_index = 0

        self.discussion.entities = [
            e for e in self.discussion.entities if e.id != entity_id
        ]
        if self.discussion.moderator_id == entity_id:
            self.discussion.moderator_id = None

        # Persist to DB if discussion is already started
        if self.discussion.id and self.discussion.status in ("active", "paused"):
            self.db.remove_discussion_member(self.discussion.id, entity_id)

            sys_msg = Message(
                entity_id=entity_id, entity_name=entity_name,
                content=f"-- {entity_name} left the discussion --",
                role=MessageRole.SYSTEM,
            )
            self.discussion.messages.append(sys_msg)
            self.db.add_message(
                self.discussion.id, entity_id,
                f"-- {entity_name} left the discussion --",
                "system", turn_number=self.discussion.turn_number,
            )

        self._notify()
        return True

    def set_moderator(self, entity_id: int,
                      also_participant: bool = False) -> bool:
        """Designate an entity as the moderator."""
        entity = self.discussion.get_entity(entity_id)
        if entity:
            self.discussion.moderator_id = entity_id
            self._notify()
            return True
        return False

    def set_topic(self, topic: str) -> bool:
        """Set the discussion topic."""
        self.discussion.topic = topic
        self._notify()
        return True

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    def start_discussion(self, moderator_participates: bool = False) -> dict:
        """Start a new discussion with the configured entities and topic."""
        if not self.discussion.topic:
            return {"error": "No topic set"}
        if len(self.discussion.entities) < 2:
            return {"error": "Need at least 2 participants"}
        if not self.discussion.moderator_id:
            return {"error": "No moderator designated"}

        mod = self.discussion.moderator
        if not mod:
            return {"error": "Moderator entity not found"}

        # Validate all entities still exist in the database
        for e in self.discussion.entities:
            if not self.db.get_entity(e.id):
                return {"error": f"Entity '{e.name}' (id={e.id}) no longer exists"}

        # Clear any stale state from a previous discussion
        self.discussion.messages.clear()
        self.discussion.storyboard.clear()
        self.discussion.turn_order.clear()

        # Create DB record
        did = self.db.create_discussion(
            self.discussion.topic, self.discussion.moderator_id,
        )
        self.discussion.id = did
        self.db.update_discussion(did, status="active", started_at=time.time())

        # Build turn order
        turn_pos = 0
        for e in self.discussion.entities:
            is_mod = e.id == self.discussion.moderator_id
            in_rotation = not is_mod or moderator_participates
            self.db.add_discussion_member(
                did, e.id,
                is_moderator=is_mod,
                also_participant=moderator_participates if is_mod else True,
                turn_position=turn_pos if in_rotation else None,
            )
            if in_rotation:
                self.discussion.turn_order.append(e.id)
                turn_pos += 1

        self.discussion.current_turn_index = 0
        self.discussion.turn_number = 1
        self.discussion.is_active = True
        self.discussion.status = "active"

        # Opening message from moderator
        target_type = "ai" if mod.entity_type == EntityType.AI else "human"
        open_prompt = self.moderator.resolve_prompt(
            "moderator", target_type, "open",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=", ".join(
                e.name for e in self.discussion.entities if e.id != mod.id
            ),
        )
        if not open_prompt:
            open_prompt = (
                f"Welcome to this discussion on: **{self.discussion.topic}**\n\n"
                f"Let's begin."
            )

        opening = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=open_prompt, role=MessageRole.MODERATOR,
        )
        self.discussion.messages.append(opening)
        self.db.add_message(
            did, mod.id, open_prompt, "moderator",
            turn_number=0,
        )

        self._notify()
        return self.get_state()

    def submit_human_message(self, entity_id: int, content: str) -> dict:
        """Submit a message from a human participant."""
        entity = self.discussion.get_entity(entity_id)
        if not entity:
            return {"error": "Entity not found"}

        current = self.discussion.current_speaker
        if not current or current.id != entity_id:
            return {"error": f"It's not {entity.name}'s turn"}

        msg = Message(
            entity_id=entity_id, entity_name=entity.name,
            content=content, role=MessageRole.PARTICIPANT,
        )
        self.discussion.messages.append(msg)
        self.db.add_message(
            self.discussion.id, entity_id, content, "participant",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return msg.to_dict()

    def submit_moderator_message(self, content: str) -> dict:
        """Submit a message from the human moderator."""
        mod = self.discussion.moderator
        if not mod:
            return {"error": "No moderator"}

        msg = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=content, role=MessageRole.MODERATOR,
        )
        self.discussion.messages.append(msg)
        self.db.add_message(
            self.discussion.id, mod.id, content, "moderator",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return msg.to_dict()

    async def generate_ai_turn(self) -> dict:
        """Generate an AI participant's contribution for the current turn."""
        current = self.discussion.current_speaker
        if not current:
            return {"error": "No current speaker"}
        if current.entity_type != EntityType.AI:
            return {"error": f"{current.name} is human - waiting for input"}

        try:
            resp = await self.moderator.generate_turn(current)

            # Detect if the participant chose to pass
            is_pass = _is_pass(resp.content)
            content = (f"*{current.name} passed this round.*"
                       if is_pass else resp.content)

            # Serialize tool call records if any
            tool_calls_json = ""
            if resp.tool_calls:
                tool_calls_json = json.dumps(
                    [tc.to_dict() for tc in resp.tool_calls]
                )

            msg = Message(
                entity_id=current.id, entity_name=current.name,
                content=content, role=MessageRole.PARTICIPANT,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                tool_calls_json=tool_calls_json,
            )
            self.discussion.messages.append(msg)

            prompt_id = self.moderator.prompt_id("participant", "ai", "turn")
            self.db.add_message(
                self.discussion.id, current.id, content, "participant",
                turn_number=self.discussion.turn_number,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                temperature_used=current.ai_config.temperature if current.ai_config else 0,
                prompt_id=prompt_id,
                tool_calls_json=tool_calls_json,
            )
            self._notify()
            result = msg.to_dict()
            if is_pass:
                result["passed"] = True
            if resp.warning:
                result["warning"] = resp.warning
            return result
        except Exception as e:
            logger.exception("AI generation failed for %s", current.name)
            return {"error": f"AI generation failed: {e}"}

    async def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete the current turn: generate or accept summary, advance turn order."""
        mod = self.discussion.moderator
        summary_text = ""

        # Capture the current speaker before summary generation changes messages
        current = self.discussion.current_speaker
        speaker_name = current.name if current else "Unknown"
        speaker_id = current.id if current else 0

        # Check if the last participant message was a pass
        last_msg = self.discussion.messages[-1] if self.discussion.messages else None
        participant_passed = (last_msg and last_msg.role == MessageRole.PARTICIPANT
                              and _is_pass(last_msg.content))

        if participant_passed and mod:
            # No AI summary needed — just note the pass
            summary_text = f"{speaker_name} passed this round."
            self.db.add_message(
                self.discussion.id, mod.id, summary_text, "moderator",
                turn_number=self.discussion.turn_number,
            )
        elif mod and mod.entity_type == EntityType.AI and not participant_passed:
            try:
                resp = await self.moderator.generate_summary()
                summary_text = resp.content
                if summary_text:
                    prompt_id = self.moderator.prompt_id(
                        "moderator", "ai", "summarize",
                    )
                    self.db.add_message(
                        self.discussion.id, mod.id, summary_text, "moderator",
                        turn_number=self.discussion.turn_number,
                        model_used=resp.model,
                        prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens,
                        total_tokens=resp.total_tokens,
                        latency_ms=resp.latency_ms,
                        prompt_id=prompt_id,
                    )
            except Exception as e:
                logger.exception("AI summary generation failed")
                return {"error": f"Summary generation failed: {e}"}
        elif mod and moderator_summary:
            summary_text = moderator_summary
            self.db.add_message(
                self.discussion.id, mod.id, summary_text, "moderator",
                turn_number=self.discussion.turn_number,
            )
        elif not mod:
            return {"error": "No moderator designated"}
        else:
            return {
                "awaiting_moderator_summary": True,
                "state": self.get_state(),
            }

        if summary_text:
            entry = StoryboardEntry(
                turn_number=self.discussion.turn_number,
                summary=summary_text,
                speaker_name=speaker_name,
            )
            self.discussion.storyboard.append(entry)

            self.db.add_storyboard_entry(
                self.discussion.id, self.discussion.turn_number,
                summary_text, speaker_id,
            )

            summary_msg = Message(
                entity_id=mod.id, entity_name=mod.name,
                content=summary_text, role=MessageRole.MODERATOR,
            )
            self.discussion.messages.append(summary_msg)

        next_speaker = self.moderator.advance_turn()
        self._notify()

        return {
            "next_speaker": next_speaker.to_dict() if next_speaker else None,
            "turn_number": self.discussion.turn_number,
            "state": self.get_state(),
        }

    def reassign_turn(self, entity_id: int) -> dict:
        """Reassign the current turn to a different participant."""
        entity = self.moderator.reassign_turn(entity_id)
        if entity:
            self._notify()
            return {"reassigned_to": entity.to_dict(), "state": self.get_state()}
        return {"error": "Could not reassign turn"}

    async def mediate(self, context: str = "") -> dict:
        """Have the moderator intervene to mediate a conflict."""
        mod = self.discussion.moderator
        if not mod:
            return {"error": "No moderator"}

        if mod.entity_type == EntityType.AI:
            try:
                resp = await self.moderator.mediate(context)
                msg = Message(
                    entity_id=mod.id, entity_name=mod.name,
                    content=resp.content, role=MessageRole.MODERATOR,
                    model_used=resp.model,
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    total_tokens=resp.total_tokens,
                    latency_ms=resp.latency_ms,
                )
                self.discussion.messages.append(msg)
                prompt_id = self.moderator.prompt_id(
                    "moderator", "ai", "mediate",
                )
                self.db.add_message(
                    self.discussion.id, mod.id, resp.content, "moderator",
                    turn_number=self.discussion.turn_number,
                    model_used=resp.model,
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    total_tokens=resp.total_tokens,
                    latency_ms=resp.latency_ms,
                    prompt_id=prompt_id,
                )
                self._notify()
                return msg.to_dict()
            except Exception as e:
                logger.exception("Mediation failed")
                return {"error": f"Mediation failed: {e}"}
        return {"awaiting_human_moderator": True}

    async def conclude_discussion(self) -> dict:
        """End the discussion, generating a final synthesis if the moderator is AI."""
        mod = self.discussion.moderator
        if mod and mod.entity_type == EntityType.AI:
            try:
                resp = await self.moderator.generate_conclusion()
                conclusion = resp.content
                msg = Message(
                    entity_id=mod.id, entity_name=mod.name,
                    content=f"## Final Synthesis\n\n{conclusion}",
                    role=MessageRole.MODERATOR,
                    model_used=resp.model,
                )
                self.discussion.messages.append(msg)
                self.db.add_message(
                    self.discussion.id, mod.id,
                    f"## Final Synthesis\n\n{conclusion}", "moderator",
                    turn_number=self.discussion.turn_number,
                    model_used=resp.model,
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    total_tokens=resp.total_tokens,
                    latency_ms=resp.latency_ms,
                )

                entry = StoryboardEntry(
                    turn_number=self.discussion.turn_number,
                    summary=f"CONCLUSION: {conclusion}",
                    speaker_name=mod.name,
                )
                self.discussion.storyboard.append(entry)
                self.db.add_storyboard_entry(
                    self.discussion.id, self.discussion.turn_number,
                    f"CONCLUSION: {conclusion}", mod.id,
                )
            except Exception as e:
                logger.exception("Conclusion generation failed")
                # Continue to mark discussion as concluded even if AI fails

        self.discussion.is_active = False
        self.discussion.status = "concluded"
        if self.discussion.id:
            self.db.update_discussion(
                self.discussion.id,
                status="concluded", ended_at=time.time(),
            )
        self._notify()
        return self.get_state()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export without mutating current state."""
        disc = self.db.get_discussion(discussion_id)
        if not disc:
            return {"error": "Discussion not found"}

        members = self.db.get_discussion_members(discussion_id)
        messages = self.db.get_messages(discussion_id)
        storyboard = self.db.get_storyboard(discussion_id)

        entities = [Entity.from_db_row(m) for m in members]
        msgs = [Message.from_db_row(m) for m in messages]
        sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

        turn_order = [
            m["entity_id"] for m in members
            if m.get("turn_position") is not None
        ]

        status = disc["status"]
        d = Discussion(
            id=discussion_id,
            topic=disc["topic"],
            entities=entities,
            moderator_id=disc.get("moderator_id"),
            messages=msgs,
            storyboard=sb,
            turn_order=turn_order,
            is_active=status == "active",
            status=status,
        )
        return d.to_dict()

    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion, restoring full state including turn position."""
        disc = self.db.get_discussion(discussion_id)
        if not disc:
            return {"error": "Discussion not found"}

        members = self.db.get_discussion_members(discussion_id)
        messages = self.db.get_messages(discussion_id)
        storyboard = self.db.get_storyboard(discussion_id)

        entities = [Entity.from_db_row(m) for m in members]
        msgs = [Message.from_db_row(m) for m in messages]
        sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

        # Restore turn order from discussion_members.turn_position
        turn_order: list[int] = [
            m["entity_id"] for m in members
            if m.get("turn_position") is not None
        ]

        status = disc["status"]
        is_active = status == "active"

        # Recover turn state for resumable discussions
        current_turn_index = 0
        turn_number = 0
        if status in ("active", "paused") and turn_order and msgs:
            turn_number = self.db.get_max_turn_number(discussion_id)
            # Find the last participant message to determine next speaker
            last_participant = next(
                (m for m in reversed(msgs)
                 if m.role == MessageRole.PARTICIPANT),
                None,
            )
            if last_participant and last_participant.entity_id in turn_order:
                last_idx = turn_order.index(last_participant.entity_id)
                current_turn_index = (last_idx + 1) % len(turn_order)
            turn_number = max(turn_number, 1)

        self.discussion = Discussion(
            id=discussion_id,
            topic=disc["topic"],
            entities=entities,
            moderator_id=disc.get("moderator_id"),
            messages=msgs,
            storyboard=sb,
            turn_order=turn_order,
            current_turn_index=current_turn_index,
            turn_number=turn_number,
            is_active=is_active,
            status=status,
        )
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
        )
        self._notify()
        return self.get_state()

    def delete_discussions(self, discussion_ids: list[int]) -> dict:
        """Soft-delete discussions by IDs."""
        count = self.db.soft_delete_discussions(discussion_ids)
        return {"deleted": count, "state": self.get_state()}

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        restored = self.db.restore_discussion(discussion_id)
        return {"restored": restored, "state": self.get_state()}

    def pause_discussion(self) -> dict:
        """Pause the current active discussion."""
        if not self.discussion.id or self.discussion.status != "active":
            return {"error": "Discussion is not active"}

        self.discussion.status = "paused"
        self.discussion.is_active = False
        self.db.update_discussion(self.discussion.id, status="paused")

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion paused --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion paused --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        if not self.discussion.id or self.discussion.status != "paused":
            return {"error": "Discussion is not paused"}

        self.discussion.status = "active"
        self.discussion.is_active = True
        self.db.update_discussion(self.discussion.id, status="active")

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion resumed --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion resumed --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion for continuation.

        Transitions the discussion to 'paused' so the user can manage
        participants before resuming with a new prompt.
        """
        if not self.discussion.id:
            return {"error": "No discussion loaded"}
        if self.discussion.status != "concluded":
            return {"error": "Discussion is not concluded"}

        self.discussion.status = "paused"
        self.discussion.is_active = False
        self.db.update_discussion(
            self.discussion.id, status="paused", ended_at=None,
        )

        # Restore turn state so the discussion can continue
        if self.discussion.turn_order:
            self.discussion.current_turn_index = 0
        self.discussion.turn_number = (
            self.db.get_max_turn_number(self.discussion.id) + 1
        )

        mod_id = self.discussion.moderator_id or 0
        sys_msg = Message(
            entity_id=mod_id, entity_name="System",
            content="-- Discussion reopened --",
            role=MessageRole.SYSTEM,
        )
        self.discussion.messages.append(sys_msg)
        self.db.add_message(
            self.discussion.id, mod_id,
            "-- Discussion reopened --", "system",
            turn_number=self.discussion.turn_number,
        )
        self._notify()
        return self.get_state()

    def reset(self) -> bool:
        """Reset to a clean state for a new discussion."""
        self.discussion = Discussion()
        self.moderator = Moderator(
            self.discussion, self.db,
            key_resolver=self._resolve_key_for_moderator,
            tool_registry=self.tool_registry,
        )
        self._notify()
        return True

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    async def list_available_tools(self) -> list[dict]:
        """Return all tools from all registered providers."""
        tools = await self.tool_registry.list_all_tools()
        return [
            {"name": t.name, "description": t.description,
             "parameters": t.parameters, "provider": t.provider_name}
            for t in tools
        ]

    def get_entity_tools(self, entity_id: int) -> list[dict]:
        """Return tool assignments for an entity."""
        return self.db.get_entity_tools(entity_id)

    def assign_tool_to_entity(self, entity_id: int, tool_name: str,
                               access_mode: str = "private") -> bool:
        """Assign a tool to an entity with the specified access mode."""
        self.db.add_entity_tool(entity_id, tool_name, access_mode)
        return True

    def remove_entity_tool(self, entity_id: int, tool_name: str) -> bool:
        """Remove a tool assignment from an entity."""
        self.db.remove_entity_tool(entity_id, tool_name)
        return True

    def set_discussion_tool_override(self, discussion_id: int, entity_id: int,
                                      tool_name: str, enabled: bool) -> bool:
        """Set a per-discussion tool override."""
        self.db.set_discussion_tool_override(
            discussion_id, entity_id, tool_name, enabled,
        )
        return True
