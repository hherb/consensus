"""Tests for context strategy UI — app methods, state shape, and setup-phase flush."""

import pytest

from consensus.app import ConsensusApp
from consensus.context_strategies import ContextStrategy


@pytest.fixture
def app(tmp_path):
    """Create a ConsensusApp with a temporary database."""
    db_path = str(tmp_path / "ctx_test.db")
    return ConsensusApp(db_path=db_path)


@pytest.fixture
def app_with_entities(app):
    """App with a provider, moderator, and two AI participants."""
    pid = app.db.add_provider("Local", "http://localhost:11434/v1", "")
    mod_id = app.db.add_entity("Moderator", "ai", "#aaa", pid, "llama3", 0.5, 512, "")
    p1_id = app.db.add_entity("Alice", "ai", "#bbb", pid, "llama3", 0.7, 1024, "")
    p2_id = app.db.add_entity("Bob", "ai", "#ccc", pid, "llama3", 0.7, 1024, "")

    app.add_to_discussion(mod_id, is_moderator=True)
    app.add_to_discussion(p1_id)
    app.add_to_discussion(p2_id)
    app.set_topic("Test topic")
    return app, mod_id, p1_id, p2_id


# --- set_default_context_strategy ---

class TestSetDefaultContextStrategy:
    def test_sets_strategy(self, app):
        result = app.set_default_context_strategy("full", 10)
        assert "error" not in result
        assert app.discussion.default_context_strategy == "full"
        assert app.discussion.default_context_window_size == 10

    def test_rejects_invalid_strategy(self, app):
        result = app.set_default_context_strategy("nonexistent")
        assert "error" in result
        # Original value unchanged
        assert app.discussion.default_context_strategy == "sliding_window"

    def test_persists_to_db_when_discussion_active(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        app.set_default_context_strategy("summary", 15)

        row = app.db.get_discussion(did)
        assert row["default_context_strategy"] == "summary"
        assert row["default_context_window_size"] == 15

    def test_all_strategies_accepted(self, app):
        for strat in ContextStrategy:
            result = app.set_default_context_strategy(strat.value)
            assert "error" not in result
            assert app.discussion.default_context_strategy == strat.value


# --- set_member_context_strategy ---

class TestSetMemberContextStrategy:
    def test_sets_member_strategy_in_memory(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        result = app.set_member_context_strategy(p1_id, "summary", 10)
        assert "error" not in result
        assert p1_id in app.discussion.member_context_configs
        assert app.discussion.member_context_configs[p1_id]["strategy"] == "summary"
        assert app.discussion.member_context_configs[p1_id]["window_size"] == 10

    def test_rejects_invalid_strategy(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        result = app.set_member_context_strategy(p1_id, "bogus")
        assert "error" in result
        assert p1_id not in app.discussion.member_context_configs

    def test_rejects_entity_not_in_discussion(self, app):
        result = app.set_member_context_strategy(9999, "full")
        assert "error" in result

    def test_persists_to_db_when_discussion_active(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        app.set_member_context_strategy(p1_id, "full", 50)

        member = app.db.get_discussion_member(did, p1_id)
        assert member["context_strategy"] == "full"
        assert member["context_window_size"] == 50


# --- State shape ---

class TestStateShape:
    def test_state_includes_embedding_available(self, app):
        state = app.get_state()
        assert "embedding_available" in state
        assert isinstance(state["embedding_available"], bool)

    def test_state_includes_member_context_configs_empty(self, app):
        state = app.get_state()
        assert "member_context_configs" in state
        assert state["member_context_configs"] == {}

    def test_state_member_configs_from_memory_during_setup(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        # No discussion.id yet (setup phase)
        assert app.discussion.id == 0

        app.set_member_context_strategy(p1_id, "summary", 15)
        state = app.get_state()

        assert str(p1_id) in state["member_context_configs"]
        cfg = state["member_context_configs"][str(p1_id)]
        assert cfg["strategy"] == "summary"
        assert cfg["window_size"] == 15

    def test_state_member_configs_from_db_when_active(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion(moderator_participates=False)
        did = app.discussion.id
        assert did > 0

        app.set_member_context_strategy(p1_id, "full", 30)
        state = app.get_state()

        assert str(p1_id) in state["member_context_configs"]
        cfg = state["member_context_configs"][str(p1_id)]
        assert cfg["strategy"] == "full"
        assert cfg["window_size"] == 30

    def test_state_default_context_fields(self, app):
        state = app.get_state()
        assert state["default_context_strategy"] == "sliding_window"
        assert state["default_context_window_size"] == 20


# --- Setup-phase flush on start_discussion ---

class TestSetupPhaseFlush:
    def test_member_configs_flushed_to_db_on_start(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities

        # Set per-entity overrides during setup
        app.set_member_context_strategy(p1_id, "summary", 10)
        app.set_member_context_strategy(p2_id, "full", 50)

        # Start discussion — should flush to DB
        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        m1 = app.db.get_discussion_member(did, p1_id)
        assert m1["context_strategy"] == "summary"
        assert m1["context_window_size"] == 10

        m2 = app.db.get_discussion_member(did, p2_id)
        assert m2["context_strategy"] == "full"
        assert m2["context_window_size"] == 50

    def test_default_strategy_persisted_on_start(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.set_default_context_strategy("summary", 8)

        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        row = app.db.get_discussion(did)
        assert row["default_context_strategy"] == "summary"
        assert row["default_context_window_size"] == 8

    def test_default_values_persisted_on_start(self, app_with_entities):
        """Even default sliding_window/20 should be written to DB."""
        app, mod_id, p1_id, p2_id = app_with_entities
        # Don't change defaults — leave as sliding_window/20
        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        row = app.db.get_discussion(did)
        assert row["default_context_strategy"] == "sliding_window"
        assert row["default_context_window_size"] == 20

    def test_entities_without_override_use_db_defaults(self, app_with_entities):
        """Entities with no explicit override should get DB column defaults."""
        app, mod_id, p1_id, p2_id = app_with_entities
        # Only set override for p1, not p2
        app.set_member_context_strategy(p1_id, "full", 100)

        app.start_discussion(moderator_participates=False)
        did = app.discussion.id

        # p2 should have DB default
        m2 = app.db.get_discussion_member(did, p2_id)
        assert m2["context_strategy"] == "sliding_window"
        assert m2["context_window_size"] == 20
