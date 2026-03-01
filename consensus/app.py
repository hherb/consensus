"""Application state and API for the discussion system."""

import time
from typing import Optional, Callable

from .models import (
    Discussion, Entity, EntityType, AIConfig,
    Message, MessageRole, StoryboardEntry,
)
from .moderator import Moderator
from .database import Database
from .config import get_db_path


class ConsensusApp:
    """Main application controller backed by SQLite."""

    def __init__(self, db_path: str = ""):
        self.db = Database(db_path or get_db_path())
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion, self.db)
        self._on_update: Optional[Callable] = None

    def set_update_callback(self, callback: Callable):
        self._on_update = callback

    def _notify(self):
        if self._on_update:
            self._on_update(self.get_state())

    # ------------------------------------------------------------------
    # State for the frontend
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        state = self.discussion.to_dict()
        state["providers"] = self.db.get_providers()
        state["saved_entities"] = self.db.get_entities()
        state["prompts"] = self.db.get_prompts()
        state["discussions_history"] = self.db.get_discussions()
        return state

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "") -> dict:
        pid = self.db.add_provider(name, base_url, api_key_env)
        return self.db.get_provider(pid)

    def update_provider(self, provider_id: str, **kwargs) -> bool:
        self.db.update_provider(provider_id, **kwargs)
        return True

    def delete_provider(self, provider_id: str) -> bool:
        self.db.delete_provider(provider_id)
        return True

    def get_providers(self) -> list[dict]:
        return self.db.get_providers()

    # ------------------------------------------------------------------
    # Entity profile management (persistent)
    # ------------------------------------------------------------------

    def save_entity(self, name: str, entity_type: str,
                    avatar_color: str = "#3b82f6",
                    provider_id: str = "", model: str = "",
                    temperature: float = 0.7, max_tokens: int = 1024,
                    system_prompt: str = "",
                    entity_id: str = "") -> dict:
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

    def delete_entity(self, entity_id: str) -> bool:
        self.db.delete_entity(entity_id)
        return True

    def get_entities(self) -> list[dict]:
        return self.db.get_entities()

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    def save_prompt(self, prompt_id: str, name: str, role: str,
                    target: str, task: str, content: str) -> dict:
        pid = self.db.save_prompt(
            prompt_id or None, name, role, target, task, content,
        )
        return self.db.get_prompt(pid)

    def delete_prompt(self, prompt_id: str) -> bool:
        self.db.delete_prompt(prompt_id)
        return True

    def get_prompts(self) -> list[dict]:
        return self.db.get_prompts()

    # ------------------------------------------------------------------
    # Discussion setup
    # ------------------------------------------------------------------

    def add_to_discussion(self, entity_id: str,
                          is_moderator: bool = False,
                          also_participant: bool = False) -> dict:
        """Add a saved entity to the current discussion."""
        row = self.db.get_entity(entity_id)
        if not row:
            return {"error": "Entity not found"}

        entity = Entity.from_db_row(row)

        # Prevent duplicates
        if self.discussion.get_entity(entity_id):
            return {"error": f"{entity.name} is already in the discussion"}

        self.discussion.entities.append(entity)

        if is_moderator:
            self.discussion.moderator_id = entity_id

        self._notify()
        return entity.to_dict()

    def remove_from_discussion(self, entity_id: str) -> bool:
        self.discussion.entities = [
            e for e in self.discussion.entities if e.id != entity_id
        ]
        if self.discussion.moderator_id == entity_id:
            self.discussion.moderator_id = None
        if entity_id in self.discussion.turn_order:
            self.discussion.turn_order.remove(entity_id)
        self._notify()
        return True

    def set_moderator(self, entity_id: str,
                      also_participant: bool = False) -> bool:
        entity = self.discussion.get_entity(entity_id)
        if entity:
            self.discussion.moderator_id = entity_id
            self._notify()
            return True
        return False

    def set_topic(self, topic: str) -> bool:
        self.discussion.topic = topic
        self._notify()
        return True

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    def start_discussion(self, moderator_participates: bool = False) -> dict:
        if not self.discussion.topic:
            return {"error": "No topic set"}
        if len(self.discussion.entities) < 2:
            return {"error": "Need at least 2 participants"}
        if not self.discussion.moderator_id:
            return {"error": "No moderator designated"}

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

        # Opening message from moderator
        mod = self.discussion.moderator
        open_prompt = self.moderator._resolve_prompt(
            "moderator", "ai" if mod.entity_type == EntityType.AI else "human",
            "open",
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

    def submit_human_message(self, entity_id: str, content: str) -> dict:
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
        current = self.discussion.current_speaker
        if not current:
            return {"error": "No current speaker"}
        if current.entity_type != EntityType.AI:
            return {"error": f"{current.name} is human - waiting for input"}

        try:
            resp = await self.moderator.generate_turn(current)
            msg = Message(
                entity_id=current.id, entity_name=current.name,
                content=resp.content, role=MessageRole.PARTICIPANT,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
            )
            self.discussion.messages.append(msg)

            prompt_id = self.moderator._prompt_id("participant", "ai", "turn")
            self.db.add_message(
                self.discussion.id, current.id, resp.content, "participant",
                turn_number=self.discussion.turn_number,
                model_used=resp.model,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                latency_ms=resp.latency_ms,
                temperature_used=current.ai_config.temperature,
                prompt_id=prompt_id,
            )
            self._notify()
            return msg.to_dict()
        except Exception as e:
            return {"error": f"AI generation failed: {e}"}

    async def complete_turn(self, moderator_summary: str = "") -> dict:
        mod = self.discussion.moderator
        summary_text = ""

        if mod and mod.entity_type == EntityType.AI:
            try:
                resp = await self.moderator.generate_summary()
                summary_text = resp.content
                if summary_text:
                    prompt_id = self.moderator._prompt_id(
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
            except Exception:
                pass
        elif moderator_summary:
            summary_text = moderator_summary
            self.db.add_message(
                self.discussion.id, mod.id, summary_text, "moderator",
                turn_number=self.discussion.turn_number,
            )
        else:
            return {
                "awaiting_moderator_summary": True,
                "state": self.get_state(),
            }

        if summary_text:
            last_msg = (
                self.discussion.messages[-1] if self.discussion.messages else None
            )
            entry = StoryboardEntry(
                turn_number=self.discussion.turn_number,
                summary=summary_text,
                speaker_name=last_msg.entity_name if last_msg else "Unknown",
            )
            self.discussion.storyboard.append(entry)

            speaker_entity = last_msg.entity_id if last_msg else ""
            self.db.add_storyboard_entry(
                self.discussion.id, self.discussion.turn_number,
                summary_text, speaker_entity,
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

    def reassign_turn(self, entity_id: str) -> dict:
        entity = self.moderator.reassign_turn(entity_id)
        if entity:
            self._notify()
            return {"reassigned_to": entity.to_dict(), "state": self.get_state()}
        return {"error": "Could not reassign turn"}

    async def mediate(self, context: str = "") -> dict:
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
                prompt_id = self.moderator._prompt_id(
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
                return {"error": f"Mediation failed: {e}"}
        return {"awaiting_human_moderator": True}

    async def conclude_discussion(self) -> dict:
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
            except Exception:
                pass

        self.discussion.is_active = False
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

    def load_discussion(self, discussion_id: str) -> dict:
        """Load a past discussion for review."""
        disc = self.db.get_discussion(discussion_id)
        if not disc:
            return {"error": "Discussion not found"}

        members = self.db.get_discussion_members(discussion_id)
        messages = self.db.get_messages(discussion_id)
        storyboard = self.db.get_storyboard(discussion_id)

        entities = [Entity.from_db_row(m) for m in members]
        msgs = [Message.from_db_row(m) for m in messages]
        sb = [StoryboardEntry.from_db_row(s) for s in storyboard]

        self.discussion = Discussion(
            id=discussion_id,
            topic=disc["topic"],
            entities=entities,
            moderator_id=disc.get("moderator_id"),
            messages=msgs,
            storyboard=sb,
            is_active=disc["status"] == "active",
        )
        self.moderator = Moderator(self.discussion, self.db)
        self._notify()
        return self.get_state()

    def reset(self) -> bool:
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion, self.db)
        self._notify()
        return True
