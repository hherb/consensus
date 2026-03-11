"""Tests for Premortem Analysis phase handlers.

Tests each handler in isolation AND verifies the refactored method
produces identical behavior to the original monolithic implementation.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.frame_premortem import FramePremortemHandler
from consensus.methods.phases.premortem_imagine import PremortemImagineHandler
from consensus.methods.phases.consolidate_premortem import ConsolidatePremortemHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def frame_handler():
    return FramePremortemHandler()


@pytest.fixture
def imagine_handler():
    return PremortemImagineHandler()


@pytest.fixture
def consolidate_handler():
    return ConsolidatePremortemHandler()


@pytest.fixture
def disc():
    method = get_method("premortem")
    d = Discussion(topic="Adopt microservices architecture",
                   discussion_method="premortem")
    d.method_state = method.init_state(d)
    return d


# -- FramePremortemHandler tests --

class TestFramePremortemHandler:
    def test_phase_metadata(self, frame_handler):
        assert frame_handler.phase.name == "frame"
        assert frame_handler.phase.display_name == "Framing"
        assert frame_handler.phase.rounds == 1

    def test_init_state(self, frame_handler, disc):
        state = frame_handler.init_state(disc)
        assert state == {"conclusion": ""}

    def test_system_prompt_returns_empty(self, frame_handler, ai_entity, disc):
        prompt = frame_handler.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_turn_prompt_returns_empty(self, frame_handler, ai_entity, disc):
        prompt = frame_handler.get_turn_prompt(ai_entity, disc)
        assert prompt == ""

    def test_process_response_captures_conclusion(self, frame_handler,
                                                   ai_entity, disc):
        content = "We should adopt microservices for all backend services."
        result = frame_handler.process_response(content, ai_entity, disc)
        assert disc.method_state["conclusion"] == content.strip()
        assert isinstance(result, ProcessedResponse)
        assert result.display_content == content

    def test_process_response_does_not_overwrite_conclusion(self, frame_handler,
                                                             ai_entity, disc):
        disc.method_state["conclusion"] = "Already set conclusion"
        content = "New conclusion text"
        frame_handler.process_response(content, ai_entity, disc)
        assert disc.method_state["conclusion"] == "Already set conclusion"

    def test_should_advance_false_without_conclusion(self, frame_handler, disc):
        disc.method_state["conclusion"] = ""
        assert frame_handler.should_advance(disc) is False

    def test_should_advance_true_with_conclusion(self, frame_handler, disc):
        disc.method_state["conclusion"] = "Some conclusion"
        assert frame_handler.should_advance(disc) is True


# -- PremortemImagineHandler tests --

class TestPremortemImagineHandler:
    def test_phase_metadata(self, imagine_handler):
        assert imagine_handler.phase.name == "premortem"
        assert imagine_handler.phase.display_name == "Premortem Narratives"
        assert imagine_handler.phase.rounds == 2

    def test_system_prompt_contains_premortem_phase(self, imagine_handler,
                                                     ai_entity, disc):
        disc.method_state["current_phase"] = "premortem"
        disc.method_state["conclusion"] = "Adopt microservices"
        prompt = imagine_handler.get_system_prompt(ai_entity, disc)
        assert "PREMORTEM PHASE" in prompt

    def test_system_prompt_contains_entity_name(self, imagine_handler,
                                                 ai_entity, disc):
        disc.method_state["conclusion"] = "Adopt microservices"
        prompt = imagine_handler.get_system_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_system_prompt_contains_topic(self, imagine_handler,
                                           ai_entity, disc):
        disc.method_state["conclusion"] = "Adopt microservices"
        prompt = imagine_handler.get_system_prompt(ai_entity, disc)
        assert "Adopt microservices architecture" in prompt

    def test_system_prompt_contains_conclusion(self, imagine_handler,
                                                ai_entity, disc):
        disc.method_state["conclusion"] = "Adopt microservices"
        prompt = imagine_handler.get_system_prompt(ai_entity, disc)
        assert "Adopt microservices" in prompt

    def test_turn_prompt_round_1(self, imagine_handler, ai_entity, disc):
        disc.method_state["phase_round"] = 1
        prompt = imagine_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "failure narrative" in prompt
        assert "specific and creative" in prompt

    def test_turn_prompt_round_2_differs(self, imagine_handler, ai_entity, disc):
        disc.method_state["phase_round"] = 2
        prompt = imagine_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "Round 2" in prompt
        assert "additional failure modes" in prompt

    def test_summary_prompt(self, imagine_handler, disc):
        prompt = imagine_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "failure" in prompt.lower()

    def test_transition_message_contains_conclusion(self, imagine_handler, disc):
        disc.method_state["conclusion"] = "Adopt microservices"
        msg = imagine_handler.get_transition_message(disc)
        assert "Adopt microservices" in msg
        assert "failed spectacularly" in msg


# -- ConsolidatePremortemHandler tests --

class TestConsolidatePremortemHandler:
    def test_phase_metadata(self, consolidate_handler):
        assert consolidate_handler.phase.name == "consolidate"
        assert consolidate_handler.phase.display_name == "Consolidation"
        assert consolidate_handler.phase.rounds == 1

    def test_system_prompt_returns_empty(self, consolidate_handler,
                                          ai_entity, disc):
        prompt = consolidate_handler.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_turn_prompt_returns_empty(self, consolidate_handler,
                                        ai_entity, disc):
        prompt = consolidate_handler.get_turn_prompt(ai_entity, disc)
        assert prompt == ""

    def test_transition_message(self, consolidate_handler, disc):
        msg = consolidate_handler.get_transition_message(disc)
        assert "Consolidation" in msg
        assert "failure narratives" in msg


# -- Equivalence tests: refactored method matches original behavior --

class TestPremortemEquivalence:
    def test_init_state_matches(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        state = method.init_state(disc)
        assert state["current_phase"] == "frame"
        assert state["conclusion"] == ""
        assert state["phase_round"] == 1

    def test_has_three_phases(self):
        method = get_method("premortem")
        assert len(method.default_phases) == 3
        names = [p.name for p in method.default_phases]
        assert names == ["frame", "premortem", "consolidate"]

    def test_frame_system_prompt_returns_empty(self, ai_entity):
        method = get_method("premortem")
        disc = Discussion(topic="test topic",
                          discussion_method="premortem")
        disc.method_state = method.init_state(disc)
        prompt = method.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_premortem_system_prompt_matches(self, ai_entity):
        method = get_method("premortem")
        disc = Discussion(topic="test topic",
                          discussion_method="premortem")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "premortem"
        disc.method_state["conclusion"] = "Our plan is X"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "PREMORTEM PHASE" in prompt
        assert "TestAI" in prompt
        assert "test topic" in prompt
        assert "Our plan is X" in prompt

    def test_phase_handlers_set(self):
        method = get_method("premortem")
        assert len(method.phase_handlers) == 3

    def test_conclusion_prompt_exists(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = {"conclusion": "Our plan is X"}
        prompt = method.get_conclusion_prompt(disc)
        assert "premortem analysis" in prompt.lower()
        assert "Our plan is X" in prompt

    def test_should_advance_frame_needs_conclusion(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)
        # No conclusion yet
        assert method.should_advance_phase(disc) is False
        # With conclusion
        disc.method_state["conclusion"] = "Some conclusion"
        assert method.should_advance_phase(disc) is True

    def test_process_response_captures_conclusion_in_frame(self, ai_entity):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)
        method.process_response("The plan is X", ai_entity, disc)
        assert disc.method_state["conclusion"] == "The plan is X"
