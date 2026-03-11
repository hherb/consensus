"""Tests for DiscussionMethod delegation to PhaseHandler instances."""

import pytest
from consensus.methods.base import DiscussionMethod, Phase, ProcessedResponse
from consensus.methods.phase_handler import PhaseHandler
from consensus.models import Discussion, Entity, EntityType


# -- Test handlers --

class AlphaHandler(PhaseHandler):
    phase = Phase(name="alpha", display_name="Alpha Phase",
                  description="First phase.", rounds=1)

    def get_system_prompt(self, entity, discussion):
        return f"Alpha system for {entity.name}"

    def get_turn_prompt(self, entity, discussion):
        return f"Alpha turn for {entity.name}"

    def get_summary_prompt(self, discussion, speaker, next_speaker):
        return f"Alpha summary: {speaker} done, {next_speaker} next"

    def init_state(self, discussion):
        return {"alpha_data": []}

    def process_response(self, content, entity, discussion):
        discussion.method_state.setdefault("alpha_data", []).append(content)
        return ProcessedResponse(display_content=content,
                                 extracted_data={"added": content})

    def get_transition_message(self, discussion):
        return "Entering Alpha phase."


class BetaHandler(PhaseHandler):
    phase = Phase(name="beta", display_name="Beta Phase",
                  description="Second phase.", rounds=2)

    def get_system_prompt(self, entity, discussion):
        alpha_data = discussion.method_state.get("alpha_data", [])
        return f"Beta system. Alpha produced: {alpha_data}"

    def get_turn_prompt(self, entity, discussion):
        return f"Beta turn for {entity.name}"

    def init_state(self, discussion):
        return {"beta_count": 0}

    def get_turn_order(self, entity_ids, discussion):
        return list(reversed(entity_ids))


class HandlerMethod(DiscussionMethod):
    """A method assembled from handlers."""
    name = "handler_test"
    display_name = "Handler Test"
    description = "Test method with handlers."
    phase_handlers = (AlphaHandler(), BetaHandler())


# -- Fixtures --

@pytest.fixture
def method():
    return HandlerMethod()

@pytest.fixture
def entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)

@pytest.fixture
def discussion(method):
    disc = Discussion(topic="test", discussion_method="handler_test")
    disc.method_state = method.init_state(disc)
    return disc


class TestDelegation:
    def test_default_phases_derived_from_handlers(self, method):
        assert len(method.default_phases) == 2
        assert method.default_phases[0].name == "alpha"
        assert method.default_phases[1].name == "beta"

    def test_init_state_merges_handlers(self, method, discussion):
        state = method.init_state(discussion)
        assert state["current_phase"] == "alpha"
        assert state["phase_round"] == 1
        assert state["alpha_data"] == []
        assert state["beta_count"] == 0

    def test_system_prompt_delegates_to_active(self, method, entity, discussion):
        prompt = method.get_system_prompt(entity, discussion)
        assert prompt == "Alpha system for TestAI"

    def test_turn_prompt_delegates_to_active(self, method, entity, discussion):
        prompt = method.get_turn_prompt(entity, discussion)
        assert prompt == "Alpha turn for TestAI"

    def test_summary_prompt_delegates(self, method, discussion):
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert prompt == "Alpha summary: Alice done, Bob next"

    def test_process_response_delegates(self, method, entity, discussion):
        result = method.process_response("hello", entity, discussion)
        assert result.extracted_data == {"added": "hello"}
        assert discussion.method_state["alpha_data"] == ["hello"]

    def test_should_advance_delegates(self, method, discussion):
        discussion.method_state["phase_round"] = 1
        assert not method.should_advance_phase(discussion)
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion)

    def test_advance_phase_works(self, method, discussion):
        discussion.method_state["phase_round"] = 2
        new_phase = method.advance_phase(discussion)
        assert new_phase.name == "beta"
        assert discussion.method_state["current_phase"] == "beta"
        assert discussion.method_state["phase_round"] == 1

    def test_delegates_to_beta_after_advance(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "beta"
        prompt = method.get_system_prompt(entity, discussion)
        assert "Beta system" in prompt

    def test_turn_order_delegates(self, method, discussion):
        discussion.method_state["current_phase"] = "beta"
        order = method.get_turn_order([1, 2, 3], discussion)
        assert order == [3, 2, 1]

    def test_transition_message_uses_new_phase_handler(self, method, discussion):
        alpha_phase = method.default_phases[0]
        msg = method.get_phase_transition_message(alpha_phase, discussion)
        assert msg == "Entering Alpha phase."

    def test_filter_context_default_passthrough(self, method, discussion):
        result = method.filter_context_message("Bob", "hi", "user", discussion)
        assert result == "hi"


class TestBackwardCompatibility:
    """Methods without handlers still work via direct override."""

    def test_open_discussion_still_works(self):
        from consensus.methods.open_discussion import OpenDiscussion
        method = OpenDiscussion()
        disc = Discussion(topic="test", discussion_method="open_discussion")
        entity = Entity(name="AI", entity_type=EntityType.AI, id=1)
        assert method.get_system_prompt(entity, disc) == ""
        assert method.get_turn_prompt(entity, disc) == ""
        assert len(method.default_phases) == 1
