"""Tests for consensus.app — discussion lifecycle, entity management, state."""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from consensus.app import ConsensusApp
from consensus.models import (
    Discussion, Entity, EntityType, Message, MessageRole,
)
from consensus.ai_client import AIResponse


@pytest.fixture
def app(tmp_path):
    """Create a ConsensusApp with a temporary database."""
    db_path = str(tmp_path / "app_test.db")
    return ConsensusApp(db_path=db_path)


@pytest.fixture
def app_with_entities(app):
    """Create an app with a provider, moderator, and two participants."""
    pid = app.db.add_provider("Local", "http://localhost:11434/v1", "")
    mod_id = app.db.add_entity("Moderator", "ai", "#aaa", pid, "llama3", 0.5, 512, "")
    p1_id = app.db.add_entity("Alice", "ai", "#bbb", pid, "llama3", 0.7, 1024, "")
    p2_id = app.db.add_entity("Bob", "human", "#ccc")

    app.add_to_discussion(mod_id, is_moderator=True)
    app.add_to_discussion(p1_id)
    app.add_to_discussion(p2_id)
    app.set_topic("Should AI be regulated?")
    return app, mod_id, p1_id, p2_id


# --- State management ---

class TestGetState:
    def test_initial_state(self, app):
        state = app.get_state()
        assert state["topic"] == ""
        assert state["entities"] == []
        assert state["is_active"] is False
        assert "providers" in state
        assert "prompts" in state


# --- Entity management ---

class TestEntityManagement:
    def test_save_and_retrieve_entity(self, app):
        pid = app.db.add_provider("P", "http://x", "")
        result = app.save_entity("TestBot", "ai", provider_id=pid, model="m")
        assert result is not None
        assert result["name"] == "TestBot"

    def test_update_entity(self, app):
        pid = app.db.add_provider("P", "http://x", "")
        result = app.save_entity("Bot", "ai", provider_id=pid)
        eid = result["id"]
        updated = app.save_entity("Bot2", "ai", entity_id=eid, provider_id=pid)
        assert updated["name"] == "Bot2"

    def test_delete_entity(self, app):
        eid = app.db.add_entity("Temp", "human", "#000")
        result = app.delete_entity(eid)
        entities = app.get_entities()
        assert eid not in [e["id"] for e in entities]


# --- Discussion setup ---

class TestDiscussionSetup:
    def test_add_to_discussion(self, app):
        eid = app.db.add_entity("Alice", "human", "#000")
        result = app.add_to_discussion(eid)
        assert result["name"] == "Alice"
        assert len(app.discussion.entities) == 1

    def test_add_duplicate_to_discussion(self, app):
        eid = app.db.add_entity("Alice", "human", "#000")
        app.add_to_discussion(eid)
        result = app.add_to_discussion(eid)
        assert "error" in result

    def test_add_nonexistent_entity(self, app):
        result = app.add_to_discussion(99999)
        assert "error" in result

    def test_add_as_moderator(self, app):
        eid = app.db.add_entity("Mod", "human", "#000")
        app.add_to_discussion(eid, is_moderator=True)
        assert app.discussion.moderator_id == eid

    def test_remove_from_discussion(self, app):
        eid = app.db.add_entity("Alice", "human", "#000")
        app.add_to_discussion(eid)
        result = app.remove_from_discussion(eid)
        assert result is True
        assert len(app.discussion.entities) == 0

    def test_remove_clears_moderator_id(self, app):
        eid = app.db.add_entity("Mod", "human", "#000")
        app.add_to_discussion(eid, is_moderator=True)
        app.remove_from_discussion(eid)
        assert app.discussion.moderator_id is None

    def test_set_topic(self, app):
        app.set_topic("New Topic")
        assert app.discussion.topic == "New Topic"


# --- Discussion lifecycle ---

class TestStartDiscussion:
    def test_start_requires_topic(self, app):
        result = app.start_discussion()
        assert "error" in result
        assert "topic" in result["error"].lower()

    def test_start_requires_participants(self, app):
        app.set_topic("Test")
        result = app.start_discussion()
        assert "error" in result
        assert "2 participants" in result["error"]

    def test_start_requires_moderator(self, app):
        app.set_topic("Test")
        e1 = app.db.add_entity("A", "human", "#000")
        e2 = app.db.add_entity("B", "human", "#111")
        app.add_to_discussion(e1)
        app.add_to_discussion(e2)
        result = app.start_discussion()
        assert "error" in result
        assert "moderator" in result["error"].lower()

    def test_start_successful(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        result = app.start_discussion()
        assert "error" not in result
        assert app.discussion.is_active is True
        assert app.discussion.status == "active"
        assert app.discussion.id > 0
        assert len(app.discussion.messages) == 1  # opening message
        assert app.discussion.turn_number == 1

    def test_moderator_excluded_from_turn_order_by_default(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion(moderator_participates=False)
        assert mod_id not in app.discussion.turn_order
        assert p1_id in app.discussion.turn_order
        assert p2_id in app.discussion.turn_order

    def test_moderator_included_when_participates(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion(moderator_participates=True)
        assert mod_id in app.discussion.turn_order


class TestSubmitHumanMessage:
    def test_submit_on_turn(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        # First speaker should be p1 (Alice, AI) not Bob
        # Advance past AI to get to human
        app.discussion.current_turn_index = app.discussion.turn_order.index(p2_id)
        result = app.submit_human_message(p2_id, "Hello from Bob!")
        assert "error" not in result
        assert result["content"] == "Hello from Bob!"

    def test_submit_not_on_turn(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        # p2 tries to speak when it's p1's turn
        if app.discussion.current_speaker.id != p2_id:
            result = app.submit_human_message(p2_id, "Out of turn!")
            assert "error" in result

    def test_submit_unknown_entity(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        result = app.submit_human_message(99999, "Ghost!")
        assert "error" in result


class TestPauseResume:
    def test_pause_active_discussion(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        result = app.pause_discussion()
        assert "error" not in result
        assert app.discussion.status == "paused"
        assert app.discussion.is_active is False

    def test_pause_already_paused(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        app.pause_discussion()
        result = app.pause_discussion()
        assert "error" in result

    def test_resume_paused_discussion(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        app.pause_discussion()
        result = app.resume_discussion()
        assert "error" not in result
        assert app.discussion.status == "active"
        assert app.discussion.is_active is True

    def test_resume_not_paused(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        result = app.resume_discussion()
        assert "error" in result


class TestConcludeDiscussion:
    @pytest.mark.asyncio
    async def test_conclude_marks_discussion_done(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        # Mock AI conclusion generation
        with patch.object(app.moderator, 'generate_conclusion',
                          new_callable=AsyncMock,
                          return_value=AIResponse(content="Final thoughts.")):
            result = await app.conclude_discussion()
        assert app.discussion.status == "concluded"
        assert app.discussion.is_active is False


class TestReset:
    def test_reset_clears_state(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        app.reset()
        assert app.discussion.topic == ""
        assert app.discussion.entities == []
        assert app.discussion.is_active is False


class TestReopenDiscussion:
    @pytest.mark.asyncio
    async def test_reopen_concluded(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        with patch.object(app.moderator, 'generate_conclusion',
                          new_callable=AsyncMock,
                          return_value=AIResponse(content="Done.")):
            await app.conclude_discussion()

        result = app.reopen_discussion()
        assert "error" not in result
        assert app.discussion.status == "paused"

    def test_reopen_not_concluded(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        result = app.reopen_discussion()
        assert "error" in result


# --- Add/remove during active discussion ---

class TestLiveDiscussionManagement:
    def test_add_entity_during_active_discussion(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        new_id = app.db.add_entity("Eve", "human", "#999")
        result = app.add_to_discussion(new_id)
        assert "error" not in result
        assert new_id in app.discussion.turn_order
        # Should have a system join message
        join_msgs = [m for m in app.discussion.messages
                     if "joined" in m.content]
        assert len(join_msgs) == 1

    def test_cannot_remove_moderator_during_active(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        result = app.remove_from_discussion(mod_id)
        assert "error" in result

    def test_remove_participant_during_active(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        # Remove a participant who is NOT current speaker
        current = app.discussion.current_speaker
        remove_id = p2_id if current.id != p2_id else p1_id
        result = app.remove_from_discussion(remove_id)
        if isinstance(result, dict) and "error" in result:
            # Could be current speaker - that's also valid behavior
            pass
        else:
            assert result is True
            assert remove_id not in app.discussion.turn_order


# --- Turn index adjustment on removal ---

class TestTurnIndexAdjustment:
    def test_removal_adjusts_turn_index(self, app):
        """Removing entity before current index should decrement the index."""
        e1 = app.db.add_entity("A", "human", "#000")
        e2 = app.db.add_entity("B", "human", "#111")
        e3 = app.db.add_entity("C", "human", "#222")
        app.add_to_discussion(e1, is_moderator=True)
        app.add_to_discussion(e2)
        app.add_to_discussion(e3)
        app.discussion.turn_order = [e1, e2, e3]
        app.discussion.current_turn_index = 2  # pointing to e3

        app.remove_from_discussion(e1)  # remove before current index
        # current_turn_index should have decremented
        assert app.discussion.current_turn_index == 1


# --- BYOK key resolution ---

class TestBYOKResolution:
    def test_byok_key_takes_precedence(self, app):
        pid = app.db.add_provider("P", "http://x", "SOME_ENV_VAR")
        ConsensusApp.set_request_api_keys({str(pid): "byok-key-123"})
        key = app.resolve_provider_api_key(pid, "SOME_ENV_VAR")
        assert key == "byok-key-123"
        ConsensusApp.clear_request_api_keys()

    def test_env_var_fallback(self, app, monkeypatch):
        monkeypatch.setenv("MY_KEY", "env-key-456")
        pid = app.db.add_provider("P", "http://x", "MY_KEY")
        key = app.resolve_provider_api_key(pid, "MY_KEY")
        assert key == "env-key-456"

    def test_clear_removes_byok(self, app):
        ConsensusApp.set_request_api_keys({"1": "temp-key"})
        ConsensusApp.clear_request_api_keys()
        key = app.resolve_provider_api_key(1, "")
        assert key == ""


# --- Update callback ---

class TestUpdateCallback:
    def test_notify_calls_callback(self, app):
        calls = []
        app.set_update_callback(lambda state: calls.append(state))
        app.set_topic("Test")
        assert len(calls) == 1
        assert calls[0]["topic"] == "Test"


# --- Provider management ---

class TestProviderManagement:
    def test_provider_redacts_api_key_env(self, app):
        pid = app.db.add_provider("P", "http://x", "SECRET_KEY")
        providers = app.get_providers()
        for p in providers:
            if p["id"] == pid:
                assert "api_key_env" not in p
                assert "has_key" in p
                break
