"""Core data models for the consensus discussion system."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import os
import time
import uuid


class EntityType(Enum):
    HUMAN = "human"
    AI = "ai"


class MessageRole(Enum):
    PARTICIPANT = "participant"
    MODERATOR = "moderator"
    SYSTEM = "system"


ENTITY_COLORS = [
    "#3b82f6", "#ef4444", "#22c55e", "#f59e0b",
    "#8b5cf6", "#ec4899", "#06b6d4", "#f97316",
]


@dataclass
class AIConfig:
    """Configuration for an AI entity, resolved from DB + environment."""
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    model: str = "llama3"
    temperature: float = 0.7
    max_tokens: int = 1024
    system_prompt: str = ""
    provider_id: str = ""

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system_prompt": self.system_prompt,
            "provider_id": self.provider_id,
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "AIConfig":
        """Build AIConfig from a joined entity+provider DB row."""
        api_key_env = row.get("api_key_env") or ""
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        return cls(
            base_url=row.get("base_url") or "http://localhost:11434/v1",
            api_key=api_key,
            model=row.get("model") or "llama3",
            temperature=row.get("temperature", 0.7) or 0.7,
            max_tokens=row.get("max_tokens", 1024) or 1024,
            system_prompt=row.get("system_prompt") or "",
            provider_id=row.get("provider_id") or "",
        )


@dataclass
class Entity:
    """A participant in the discussion."""
    name: str
    entity_type: EntityType
    ai_config: Optional[AIConfig] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    avatar_color: str = ""

    def __post_init__(self):
        if not self.avatar_color:
            self.avatar_color = ENTITY_COLORS[hash(self.id) % len(ENTITY_COLORS)]

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "avatar_color": self.avatar_color,
        }
        if self.ai_config:
            d["ai_config"] = self.ai_config.to_dict()
        return d

    @classmethod
    def from_db_row(cls, row: dict) -> "Entity":
        etype = EntityType(row["entity_type"])
        ai_config = AIConfig.from_db_row(row) if etype == EntityType.AI else None
        return cls(
            name=row["name"],
            entity_type=etype,
            ai_config=ai_config,
            id=row["id"],
            avatar_color=row.get("avatar_color") or "",
        )


@dataclass
class Message:
    """A single message in the discussion."""
    entity_id: str
    entity_name: str
    content: str
    role: MessageRole = MessageRole.PARTICIPANT
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    # AI metadata (populated only for AI-generated messages)
    model_used: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "content": self.content,
            "role": self.role.value,
            "timestamp": self.timestamp,
        }
        if self.model_used:
            d["model_used"] = self.model_used
            d["prompt_tokens"] = self.prompt_tokens
            d["completion_tokens"] = self.completion_tokens
            d["total_tokens"] = self.total_tokens
            d["latency_ms"] = self.latency_ms
        return d

    @classmethod
    def from_db_row(cls, row: dict) -> "Message":
        return cls(
            entity_id=row["entity_id"],
            entity_name=row.get("entity_name", ""),
            content=row["content"],
            role=MessageRole(row["role"]),
            timestamp=row["timestamp"],
            id=row["id"],
            model_used=row.get("model_used") or "",
            prompt_tokens=row.get("prompt_tokens") or 0,
            completion_tokens=row.get("completion_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            latency_ms=row.get("latency_ms") or 0,
        )


@dataclass
class StoryboardEntry:
    """A moderator's summary/synthesis after a turn."""
    turn_number: int
    summary: str
    speaker_name: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "turn_number": self.turn_number,
            "summary": self.summary,
            "speaker_name": self.speaker_name,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "StoryboardEntry":
        return cls(
            turn_number=row["turn_number"],
            summary=row["summary"],
            speaker_name=row.get("speaker_name") or "",
            timestamp=row["timestamp"],
        )


@dataclass
class Discussion:
    """The overall discussion state (in-memory working copy)."""
    id: str = ""
    topic: str = ""
    entities: list[Entity] = field(default_factory=list)
    moderator_id: Optional[str] = None
    messages: list[Message] = field(default_factory=list)
    storyboard: list[StoryboardEntry] = field(default_factory=list)
    turn_order: list[str] = field(default_factory=list)
    current_turn_index: int = 0
    turn_number: int = 0
    is_active: bool = False

    @property
    def moderator(self) -> Optional[Entity]:
        for e in self.entities:
            if e.id == self.moderator_id:
                return e
        return None

    @property
    def current_speaker(self) -> Optional[Entity]:
        if not self.turn_order:
            return None
        idx = self.current_turn_index % len(self.turn_order)
        speaker_id = self.turn_order[idx]
        return self.get_entity(speaker_id)

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        for e in self.entities:
            if e.id == entity_id:
                return e
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "topic": self.topic,
            "entities": [e.to_dict() for e in self.entities],
            "moderator_id": self.moderator_id,
            "messages": [m.to_dict() for m in self.messages],
            "storyboard": [s.to_dict() for s in self.storyboard],
            "turn_order": self.turn_order,
            "current_turn_index": self.current_turn_index,
            "turn_number": self.turn_number,
            "is_active": self.is_active,
            "current_speaker_id": (
                self.current_speaker.id if self.current_speaker else None
            ),
        }
