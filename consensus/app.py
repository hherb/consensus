"""Application state and API for the discussion system."""

from typing import Optional, Callable

from .models import (
    Discussion, Entity, EntityType, AIConfig,
    Message, MessageRole, StoryboardEntry,
)
from .moderator import Moderator


class ConsensusApp:
    """Main application controller."""

    def __init__(self):
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion)
        self._on_update: Optional[Callable] = None

    def set_update_callback(self, callback: Callable):
        self._on_update = callback

    def _notify(self):
        if self._on_update:
            self._on_update(self.get_state())

    def get_state(self) -> dict:
        return self.discussion.to_dict()

    def add_entity(
        self,
        name: str,
        entity_type: str,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system_prompt: str = "",
        avatar_color: str = "",
    ) -> dict:
        etype = EntityType(entity_type)
        ai_config = None
        if etype == EntityType.AI:
            ai_config = AIConfig(
                base_url=base_url or "http://localhost:11434/v1",
                api_key=api_key,
                model=model or "llama3",
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )

        entity = Entity(
            name=name,
            entity_type=etype,
            ai_config=ai_config,
            avatar_color=avatar_color,
        )
        self.discussion.entities.append(entity)
        self._notify()
        return entity.to_dict()

    def remove_entity(self, entity_id: str) -> bool:
        self.discussion.entities = [
            e for e in self.discussion.entities if e.id != entity_id
        ]
        if self.discussion.moderator_id == entity_id:
            self.discussion.moderator_id = None
        if entity_id in self.discussion.turn_order:
            self.discussion.turn_order.remove(entity_id)
        self._notify()
        return True

    def set_moderator(self, entity_id: str) -> bool:
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

    def start_discussion(self) -> dict:
        if not self.discussion.topic:
            return {"error": "No topic set"}
        if len(self.discussion.entities) < 2:
            return {"error": "Need at least 2 participants"}
        if not self.discussion.moderator_id:
            return {"error": "No moderator designated"}

        self.discussion.turn_order = [
            e.id for e in self.discussion.entities
            if e.id != self.discussion.moderator_id
        ]
        self.discussion.current_turn_index = 0
        self.discussion.turn_number = 1
        self.discussion.is_active = True

        mod = self.discussion.moderator
        participants = ", ".join(
            e.name for e in self.discussion.entities if e.id != mod.id
        )
        opening = Message(
            entity_id=mod.id,
            entity_name=mod.name,
            content=(
                f"Welcome to this discussion on: **{self.discussion.topic}**\n\n"
                f"Participants: {participants}\n\n"
                f"I will moderate this discussion, summarize key points after each turn, "
                f"and synthesize conclusions. Let's begin."
            ),
            role=MessageRole.MODERATOR,
        )
        self.discussion.messages.append(opening)
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
            entity_id=entity_id,
            entity_name=entity.name,
            content=content,
            role=MessageRole.PARTICIPANT,
        )
        self.discussion.messages.append(msg)
        self._notify()
        return msg.to_dict()

    def submit_moderator_message(self, content: str) -> dict:
        """Human moderator submits a message (summary, mediation, etc.)."""
        mod = self.discussion.moderator
        if not mod:
            return {"error": "No moderator"}

        msg = Message(
            entity_id=mod.id,
            entity_name=mod.name,
            content=content,
            role=MessageRole.MODERATOR,
        )
        self.discussion.messages.append(msg)
        self._notify()
        return msg.to_dict()

    async def generate_ai_turn(self) -> dict:
        current = self.discussion.current_speaker
        if not current:
            return {"error": "No current speaker"}
        if current.entity_type != EntityType.AI:
            return {"error": f"{current.name} is human - waiting for input"}

        try:
            content = await self.moderator.generate_turn(current)
            msg = Message(
                entity_id=current.id,
                entity_name=current.name,
                content=content,
                role=MessageRole.PARTICIPANT,
            )
            self.discussion.messages.append(msg)
            self._notify()
            return msg.to_dict()
        except Exception as e:
            return {"error": f"AI generation failed: {e}"}

    async def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete current turn: generate/accept summary, advance to next speaker."""
        mod = self.discussion.moderator

        summary_text = ""
        if mod and mod.entity_type == EntityType.AI:
            try:
                summary_text = await self.moderator.generate_summary()
            except Exception:
                pass
        elif moderator_summary:
            summary_text = moderator_summary
        else:
            return {
                "awaiting_moderator_summary": True,
                "state": self.get_state(),
            }

        if summary_text:
            last_msg = self.discussion.messages[-1] if self.discussion.messages else None
            entry = StoryboardEntry(
                turn_number=self.discussion.turn_number,
                summary=summary_text,
                speaker_name=last_msg.entity_name if last_msg else "Unknown",
            )
            self.discussion.storyboard.append(entry)

            summary_msg = Message(
                entity_id=mod.id,
                entity_name=mod.name,
                content=summary_text,
                role=MessageRole.MODERATOR,
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
                text = await self.moderator.mediate(context)
                msg = Message(
                    entity_id=mod.id,
                    entity_name=mod.name,
                    content=text,
                    role=MessageRole.MODERATOR,
                )
                self.discussion.messages.append(msg)
                self._notify()
                return msg.to_dict()
            except Exception as e:
                return {"error": f"Mediation failed: {e}"}
        return {"awaiting_human_moderator": True}

    async def conclude_discussion(self) -> dict:
        mod = self.discussion.moderator
        if mod and mod.entity_type == EntityType.AI:
            try:
                conclusion = await self.moderator.generate_conclusion()
                msg = Message(
                    entity_id=mod.id,
                    entity_name=mod.name,
                    content=f"## Final Synthesis\n\n{conclusion}",
                    role=MessageRole.MODERATOR,
                )
                self.discussion.messages.append(msg)

                entry = StoryboardEntry(
                    turn_number=self.discussion.turn_number,
                    summary=f"CONCLUSION: {conclusion}",
                    speaker_name=mod.name,
                )
                self.discussion.storyboard.append(entry)
            except Exception:
                pass

        self.discussion.is_active = False
        self._notify()
        return self.get_state()

    def reset(self) -> bool:
        self.discussion = Discussion()
        self.moderator = Moderator(self.discussion)
        self._notify()
        return True
