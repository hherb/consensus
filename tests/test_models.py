"""Tests for consensus.models — data model serialization, properties, edge cases."""

import json
import os
import time

import pytest

from consensus.models import (
    AIConfig, Discussion, Entity, EntityType, Message, MessageRole,
    StoryboardEntry, resolve_api_key,
    DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS,
    ENTITY_COLORS,
)


# --- resolve_api_key ---

class TestResolveApiKey:
    def test_empty_env_var(self):
        assert resolve_api_key("") == ""

    def test_missing_env_var(self):
        assert resolve_api_key("NONEXISTENT_KEY_12345") == ""

    def test_present_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_KEY", "sk-123")
        assert resolve_api_key("MY_TEST_KEY") == "sk-123"


# --- AIConfig ---

class TestAIConfig:
    def test_to_dict_excludes_api_key(self):
        cfg = AIConfig(api_key="secret")
        d = cfg.to_dict()
        assert "api_key" not in d
        assert "base_url" in d

    def test_from_db_row_defaults(self):
        row = {}
        cfg = AIConfig.from_db_row(row)
        assert cfg.base_url == DEFAULT_BASE_URL
        assert cfg.model == DEFAULT_MODEL
        assert cfg.temperature == DEFAULT_TEMPERATURE
        assert cfg.max_tokens == DEFAULT_MAX_TOKENS
        assert cfg.api_key == ""

    def test_from_db_row_with_values(self, monkeypatch):
        monkeypatch.setenv("OPENAI_KEY", "sk-test")
        row = {
            "base_url": "http://custom:8080/v1",
            "api_key_env": "OPENAI_KEY",
            "model": "gpt-4",
            "temperature": 0.3,
            "max_tokens": 2048,
            "system_prompt": "Be helpful.",
            "provider_id": 5,
        }
        cfg = AIConfig.from_db_row(row)
        assert cfg.base_url == "http://custom:8080/v1"
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-4"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2048
        assert cfg.system_prompt == "Be helpful."
        assert cfg.provider_id == 5

    def test_from_db_row_zero_temperature(self):
        """Temperature=0 is a valid value and should NOT fall back to default."""
        row = {"temperature": 0, "max_tokens": 100}
        cfg = AIConfig.from_db_row(row)
        assert cfg.temperature == 0
        assert cfg.max_tokens == 100

    def test_from_db_row_none_temperature_falls_back(self):
        row = {"temperature": None, "max_tokens": None}
        cfg = AIConfig.from_db_row(row)
        assert cfg.temperature == DEFAULT_TEMPERATURE
        assert cfg.max_tokens == DEFAULT_MAX_TOKENS


# --- Entity ---

class TestEntity:
    def test_human_entity_no_ai_config(self):
        row = {"id": 1, "name": "Alice", "entity_type": "human", "avatar_color": "#abc"}
        e = Entity.from_db_row(row)
        assert e.entity_type == EntityType.HUMAN
        assert e.ai_config is None

    def test_ai_entity_has_config(self):
        row = {
            "id": 2, "name": "Bot", "entity_type": "ai", "avatar_color": "#fff",
            "base_url": None, "api_key_env": "", "model": None,
            "temperature": None, "max_tokens": None,
            "system_prompt": "", "provider_id": 0,
        }
        e = Entity.from_db_row(row)
        assert e.entity_type == EntityType.AI
        assert e.ai_config is not None

    def test_to_dict_includes_ai_config_when_present(self):
        cfg = AIConfig(model="gpt-4")
        e = Entity(name="Bot", entity_type=EntityType.AI, ai_config=cfg, id=1)
        d = e.to_dict()
        assert "ai_config" in d
        assert d["ai_config"]["model"] == "gpt-4"

    def test_to_dict_excludes_ai_config_when_absent(self):
        e = Entity(name="Human", entity_type=EntityType.HUMAN, id=1)
        d = e.to_dict()
        assert "ai_config" not in d

    def test_avatar_color_auto_assigned(self):
        e = Entity(name="X", entity_type=EntityType.HUMAN, id=3)
        assert e.avatar_color in ENTITY_COLORS

    def test_avatar_color_preserved_if_set(self):
        e = Entity(name="X", entity_type=EntityType.HUMAN, id=3, avatar_color="#custom")
        assert e.avatar_color == "#custom"


# --- Message ---

class TestMessage:
    def test_to_dict_basic(self):
        msg = Message(entity_id=1, entity_name="Alice", content="Hello",
                      role=MessageRole.PARTICIPANT, id=10)
        d = msg.to_dict()
        assert d["entity_name"] == "Alice"
        assert d["role"] == "participant"
        assert "model_used" not in d  # no AI metadata

    def test_to_dict_with_ai_metadata(self):
        msg = Message(entity_id=1, entity_name="Bot", content="Hi",
                      model_used="gpt-4", prompt_tokens=10,
                      completion_tokens=20, total_tokens=30, latency_ms=100)
        d = msg.to_dict()
        assert d["model_used"] == "gpt-4"
        assert d["total_tokens"] == 30

    def test_to_dict_with_valid_tool_calls_json(self):
        calls = [{"tool_name": "search", "result": "ok"}]
        msg = Message(entity_id=1, entity_name="Bot", content="X",
                      tool_calls_json=json.dumps(calls))
        d = msg.to_dict()
        assert d["tool_calls"] == calls

    def test_to_dict_with_invalid_tool_calls_json(self):
        msg = Message(entity_id=1, entity_name="Bot", content="X",
                      tool_calls_json="not-valid-json{{{")
        d = msg.to_dict()
        assert d["tool_calls"] == []

    def test_from_db_row(self):
        row = {
            "id": 5, "entity_id": 1, "entity_name": "Alice",
            "content": "Hello", "role": "moderator",
            "timestamp": 1000.0, "model_used": "gpt-4",
            "prompt_tokens": 10, "completion_tokens": 20,
            "total_tokens": 30, "latency_ms": 50,
            "tool_calls_json": "",
        }
        msg = Message.from_db_row(row)
        assert msg.role == MessageRole.MODERATOR
        assert msg.model_used == "gpt-4"

    def test_from_db_row_missing_optional_fields(self):
        row = {
            "id": 1, "entity_id": 2, "content": "Hi",
            "role": "participant", "timestamp": 1000.0,
        }
        msg = Message.from_db_row(row)
        assert msg.entity_name == ""
        assert msg.model_used == ""
        assert msg.tool_calls_json == ""


# --- StoryboardEntry ---

class TestStoryboardEntry:
    def test_roundtrip(self):
        entry = StoryboardEntry(turn_number=3, summary="Good point",
                                speaker_name="Alice", timestamp=1000.0)
        d = entry.to_dict()
        rebuilt = StoryboardEntry.from_db_row(d)
        assert rebuilt.turn_number == 3
        assert rebuilt.summary == "Good point"
        assert rebuilt.speaker_name == "Alice"


# --- Discussion ---

class TestDiscussion:
    def _make_discussion(self):
        e1 = Entity(name="Mod", entity_type=EntityType.AI, id=1,
                     ai_config=AIConfig())
        e2 = Entity(name="Alice", entity_type=EntityType.HUMAN, id=2)
        e3 = Entity(name="Bob", entity_type=EntityType.HUMAN, id=3)
        return Discussion(
            id=1, topic="Test", entities=[e1, e2, e3],
            moderator_id=1, turn_order=[2, 3],
            current_turn_index=0, turn_number=1,
            is_active=True, status="active",
        )

    def test_moderator_property(self):
        d = self._make_discussion()
        assert d.moderator is not None
        assert d.moderator.name == "Mod"

    def test_moderator_returns_none_when_missing(self):
        d = Discussion(entities=[], moderator_id=999)
        assert d.moderator is None

    def test_current_speaker(self):
        d = self._make_discussion()
        assert d.current_speaker.name == "Alice"  # turn_order[0] = id 2

    def test_current_speaker_wraps_around(self):
        d = self._make_discussion()
        d.current_turn_index = 5  # 5 % 2 = 1 -> id 3 = Bob
        assert d.current_speaker.name == "Bob"

    def test_current_speaker_none_when_no_turn_order(self):
        d = Discussion()
        assert d.current_speaker is None

    def test_get_entity_found(self):
        d = self._make_discussion()
        assert d.get_entity(2).name == "Alice"

    def test_get_entity_not_found(self):
        d = self._make_discussion()
        assert d.get_entity(999) is None

    def test_to_dict_complete(self):
        d = self._make_discussion()
        out = d.to_dict()
        assert out["topic"] == "Test"
        assert out["is_active"] is True
        assert out["is_paused"] is False
        assert out["current_speaker_id"] == 2
        assert len(out["entities"]) == 3

    def test_to_dict_paused(self):
        d = self._make_discussion()
        d.status = "paused"
        out = d.to_dict()
        assert out["is_paused"] is True
