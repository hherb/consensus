"""Tests for Key Assumptions Check phase handlers.

Tests each handler in isolation AND verifies the refactored method
produces identical behavior to the original monolithic implementation.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.surface_assumptions import SurfaceAssumptionsHandler
from consensus.methods.phases.challenge_assumptions import ChallengeAssumptionsHandler
from consensus.methods.phases.assess_assumptions import AssessAssumptionsHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def surface_handler():
    return SurfaceAssumptionsHandler()


@pytest.fixture
def challenge_handler():
    return ChallengeAssumptionsHandler()


@pytest.fixture
def assess_handler():
    return AssessAssumptionsHandler()


@pytest.fixture
def disc():
    method = get_method("key_assumptions")
    d = Discussion(topic="Will AI replace programmers?",
                   discussion_method="key_assumptions")
    d.method_state = method.init_state(d)
    return d


# -- SurfaceAssumptionsHandler tests --

class TestSurfaceAssumptionsHandler:
    def test_system_prompt_contains_assumption_surfacing(self, surface_handler,
                                                          ai_entity, disc):
        prompt = surface_handler.get_system_prompt(ai_entity, disc)
        assert "ASSUMPTION SURFACING" in prompt

    def test_system_prompt_contains_entity_name(self, surface_handler,
                                                 ai_entity, disc):
        prompt = surface_handler.get_system_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_system_prompt_contains_topic(self, surface_handler,
                                           ai_entity, disc):
        prompt = surface_handler.get_system_prompt(ai_entity, disc)
        assert "Will AI replace programmers?" in prompt

    def test_turn_prompt_contains_entity_name(self, surface_handler,
                                               ai_entity, disc):
        prompt = surface_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_process_response_extracts_assumptions(self, surface_handler,
                                                    ai_entity, disc):
        content = (
            "Key assumptions:\n"
            "1. The market will continue to grow at current rates\n"
            "2. Our competitors will not significantly change strategy\n"
            "3. The regulatory environment will remain stable"
        )
        result = surface_handler.process_response(content, ai_entity, disc)
        assumptions = disc.method_state["assumptions"]
        assert len(assumptions) == 3
        assert "market" in assumptions[0].lower()
        assert isinstance(result, ProcessedResponse)

    def test_process_response_deduplicates(self, surface_handler,
                                            ai_entity, disc):
        disc.method_state["assumptions"] = [
            "The market will continue to grow at current rates"
        ]
        content = "1. The market will continue to grow at current rates steadily"
        surface_handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["assumptions"]) == 1

        content = "1. Competitors will not enter the market segment"
        surface_handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["assumptions"]) == 2

    def test_should_advance_requires_assumptions_and_round_gt_1(
            self, surface_handler, disc):
        # No assumptions, round 1
        assert surface_handler.should_advance(disc) is False

        # Assumptions but round 1
        disc.method_state["assumptions"] = ["Some assumption here"]
        assert surface_handler.should_advance(disc) is False

        # Assumptions and round > 1
        disc.method_state["phase_round"] = 2
        assert surface_handler.should_advance(disc) is True

    def test_should_advance_false_when_round_gt_1_but_no_assumptions(
            self, surface_handler, disc):
        disc.method_state["phase_round"] = 2
        disc.method_state["assumptions"] = []
        assert surface_handler.should_advance(disc) is False

    def test_init_state(self, surface_handler, disc):
        state = surface_handler.init_state(disc)
        assert state == {"assumptions": []}

    def test_summary_prompt(self, surface_handler, disc):
        prompt = surface_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


# -- ChallengeAssumptionsHandler tests --

class TestChallengeAssumptionsHandler:
    def test_system_prompt_includes_assumption_list(self, challenge_handler,
                                                     ai_entity, disc):
        disc.method_state["current_phase"] = "challenge"
        disc.method_state["assumptions"] = [
            "Growth continues", "No competition change"
        ]
        prompt = challenge_handler.get_system_prompt(ai_entity, disc)
        assert "A1:" in prompt
        assert "A2:" in prompt
        assert "Growth continues" in prompt
        assert "ASSUMPTION CHALLENGE" in prompt

    def test_system_prompt_includes_falsification(self, challenge_handler,
                                                   ai_entity, disc):
        disc.method_state["current_phase"] = "challenge"
        disc.method_state["assumptions"] = ["Some assumption text here"]
        prompt = challenge_handler.get_system_prompt(ai_entity, disc)
        assert "Falsification" in prompt or "falsification" in prompt

    def test_transition_message_lists_assumption_count(self, challenge_handler,
                                                       disc):
        disc.method_state["assumptions"] = ["A1", "A2", "A3"]
        msg = challenge_handler.get_transition_message(disc)
        assert "3" in msg
        assert "assumptions" in msg.lower()

    def test_transition_message_explains_empty_assumptions(
            self, challenge_handler, disc):
        """After a MAX_SURFACE_ROUNDS give-up (PR #39 review) the
        transition must explain the empty list, not announce
        '0 assumptions have been surfaced:' with nothing below it."""
        disc.method_state["assumptions"] = []
        msg = challenge_handler.get_transition_message(disc)
        assert "0 assumptions have been surfaced" not in msg
        assert "no assumptions" in msg.lower()

    def test_prompts_handle_empty_assumptions(
            self, challenge_handler, ai_entity, disc):
        """The system and turn prompts must match the empty-list
        transition message instead of rendering a blank assumption
        list with per-assumption instructions (PR #39 review)."""
        disc.method_state["current_phase"] = "challenge"
        disc.method_state["assumptions"] = []
        sys_prompt = challenge_handler.get_system_prompt(ai_entity, disc)
        assert "have been surfaced:\n\n" not in sys_prompt
        assert "no assumptions" in sys_prompt.lower()
        turn_prompt = challenge_handler.get_turn_prompt(ai_entity, disc)
        assert "each surfaced assumption" not in turn_prompt
        assert "assumptions you consider most critical" in turn_prompt

    def test_turn_prompt(self, challenge_handler, ai_entity, disc):
        prompt = challenge_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "challenge" in prompt.lower()

    def test_summary_prompt(self, challenge_handler, disc):
        prompt = challenge_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


# -- AssessAssumptionsHandler tests --

class TestAssessAssumptionsHandler:
    def test_system_prompt_returns_empty(self, assess_handler, ai_entity, disc):
        disc.method_state["current_phase"] = "assess"
        prompt = assess_handler.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_turn_prompt_returns_empty(self, assess_handler, ai_entity, disc):
        disc.method_state["current_phase"] = "assess"
        prompt = assess_handler.get_turn_prompt(ai_entity, disc)
        assert prompt == ""

    def test_transition_message(self, assess_handler, disc):
        msg = assess_handler.get_transition_message(disc)
        assert "assess" in msg.lower() or "Assessment" in msg


# -- Equivalence tests: refactored method matches original behavior --

class TestKeyAssumptionsEquivalence:
    def test_init_state_matches(self):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        state = method.init_state(disc)
        assert state["current_phase"] == "surface"
        assert state["assumptions"] == []
        assert state["phase_round"] == 1

    def test_has_three_phases(self):
        method = get_method("key_assumptions")
        assert len(method.default_phases) == 3
        names = [p.name for p in method.default_phases]
        assert names == ["surface", "challenge", "assess"]

    def test_surface_system_prompt_matches(self, ai_entity):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test topic",
                          discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "ASSUMPTION SURFACING" in prompt
        assert "TestAI" in prompt
        assert "test topic" in prompt

    def test_phase_handlers_set(self):
        method = get_method("key_assumptions")
        assert len(method.phase_handlers) == 3

    def test_conclusion_prompt_exists(self):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        disc.method_state = {"assumptions": ["A1", "A2"]}
        prompt = method.get_conclusion_prompt(disc)
        assert "Key Assumptions Check" in prompt
        assert "A1" in prompt
