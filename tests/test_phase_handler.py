"""Tests for the PhaseHandler ABC."""

import pytest
from consensus.methods.phase_handler import PhaseHandler
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


class ConcreteHandler(PhaseHandler):
    """Minimal concrete handler for testing."""
    phase = Phase(name="test", display_name="Test Phase",
                  description="A test phase.", rounds=2)

    def get_system_prompt(self, entity, discussion):
        return f"System prompt for {entity.name}"

    def get_turn_prompt(self, entity, discussion):
        return f"Your turn, {entity.name}"


@pytest.fixture
def handler():
    return ConcreteHandler()


@pytest.fixture
def entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def discussion():
    disc = Discussion(topic="test", discussion_method="test_method")
    disc.method_state = {"current_phase": "test", "phase_round": 1}
    return disc


class TestPhaseHandlerDefaults:
    def test_get_system_prompt(self, handler, entity, discussion):
        assert handler.get_system_prompt(entity, discussion) == "System prompt for TestAI"

    def test_get_turn_prompt(self, handler, entity, discussion):
        assert handler.get_turn_prompt(entity, discussion) == "Your turn, TestAI"

    def test_get_summary_prompt_default_empty(self, handler, discussion):
        assert handler.get_summary_prompt(discussion, "A", "B") == ""

    def test_filter_context_message_default_passthrough(self, handler, discussion):
        assert handler.filter_context_message("Bob", "hello", "user", discussion) == "hello"

    def test_process_response_default(self, handler, entity, discussion):
        result = handler.process_response("content", entity, discussion)
        assert isinstance(result, ProcessedResponse)
        assert result.display_content == "content"

    def test_init_state_default_empty(self, handler, discussion):
        assert handler.init_state(discussion) == {}

    def test_get_turn_order_default_passthrough(self, handler, discussion):
        assert handler.get_turn_order([1, 2, 3], discussion) == [1, 2, 3]


class TestPhaseHandlerAdvance:
    def test_should_advance_false_when_rounds_not_exceeded(self, handler, discussion):
        discussion.method_state["phase_round"] = 1
        assert not handler.should_advance(discussion)

    def test_should_advance_false_at_round_limit(self, handler, discussion):
        discussion.method_state["phase_round"] = 2
        assert not handler.should_advance(discussion)

    def test_should_advance_true_when_exceeded(self, handler, discussion):
        discussion.method_state["phase_round"] = 3
        assert handler.should_advance(discussion)

    def test_should_advance_false_for_unlimited(self, discussion):
        class UnlimitedHandler(ConcreteHandler):
            phase = Phase(name="open", display_name="Open", rounds=0)
        h = UnlimitedHandler()
        discussion.method_state["phase_round"] = 100
        assert not h.should_advance(discussion)


class TestPhaseHandlerTransition:
    def test_transition_message(self, handler, discussion):
        msg = handler.get_transition_message(discussion)
        assert "Test Phase" in msg
        assert "A test phase" in msg


class TestPhaseHandlerABC:
    def test_cannot_instantiate_without_prompts(self):
        with pytest.raises(TypeError):
            class Incomplete(PhaseHandler):
                phase = Phase(name="x", display_name="X")
            Incomplete()
