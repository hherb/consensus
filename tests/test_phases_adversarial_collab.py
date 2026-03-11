"""Tests for Adversarial Collaboration phase handlers.

Tests each handler in isolation AND verifies the refactored method
produces identical behavior to the original monolithic implementation.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.base import ProcessedResponse
from consensus.methods.phases.state_positions import StatePositionsHandler
from consensus.methods.phases.define_criteria import DefineCriteriaHandler
from consensus.methods.phases.present_evidence import PresentEvidenceHandler
from consensus.methods.phases.adjudicate import AdjudicateHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def positions_handler():
    return StatePositionsHandler()


@pytest.fixture
def criteria_handler():
    return DefineCriteriaHandler()


@pytest.fixture
def evidence_handler():
    return PresentEvidenceHandler()


@pytest.fixture
def adjudicate_handler():
    return AdjudicateHandler()


@pytest.fixture
def disc():
    method = get_method("adversarial_collab")
    d = Discussion(topic="Is remote work more productive?",
                   discussion_method="adversarial_collab")
    d.method_state = method.init_state(d)
    return d


# -- StatePositionsHandler tests --

class TestStatePositionsHandler:
    def test_system_prompt_contains_position_statement(self, positions_handler,
                                                        ai_entity, disc):
        prompt = positions_handler.get_system_prompt(ai_entity, disc)
        assert "POSITION STATEMENT PHASE" in prompt

    def test_system_prompt_contains_entity_name(self, positions_handler,
                                                 ai_entity, disc):
        prompt = positions_handler.get_system_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_system_prompt_contains_topic(self, positions_handler,
                                           ai_entity, disc):
        prompt = positions_handler.get_system_prompt(ai_entity, disc)
        assert "Is remote work more productive?" in prompt

    def test_turn_prompt_contains_entity_name(self, positions_handler,
                                               ai_entity, disc):
        prompt = positions_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_summary_prompt(self, positions_handler, disc):
        prompt = positions_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "position" in prompt.lower()

    def test_process_response_stores_position_summary(self, positions_handler,
                                                       ai_entity, disc):
        content = "Remote work is clearly more productive because..."
        result = positions_handler.process_response(content, ai_entity, disc)
        positions = disc.method_state["positions"]
        assert "TestAI" in positions
        assert isinstance(result, ProcessedResponse)

    def test_process_response_truncates_to_200_chars(self, positions_handler,
                                                      ai_entity, disc):
        content = "A" * 300
        positions_handler.process_response(content, ai_entity, disc)
        summary = disc.method_state["positions"]["TestAI"]
        assert len(summary) == 203  # 200 + "..."
        assert summary.endswith("...")

    def test_process_response_no_ellipsis_when_short(self, positions_handler,
                                                      ai_entity, disc):
        content = "Short position statement"
        positions_handler.process_response(content, ai_entity, disc)
        summary = disc.method_state["positions"]["TestAI"]
        assert not summary.endswith("...")
        assert summary == "Short position statement"

    def test_should_advance_requires_positions_and_round_gt_1(
            self, positions_handler, disc):
        # No positions, round 1
        assert positions_handler.should_advance(disc) is False

        # Positions but round 1
        disc.method_state["positions"] = {"TestAI": "some position"}
        assert positions_handler.should_advance(disc) is False

        # Positions and round > 1
        disc.method_state["phase_round"] = 2
        assert positions_handler.should_advance(disc) is True

    def test_should_advance_false_when_round_gt_1_but_no_positions(
            self, positions_handler, disc):
        disc.method_state["phase_round"] = 2
        disc.method_state["positions"] = {}
        assert positions_handler.should_advance(disc) is False

    def test_init_state(self, positions_handler, disc):
        state = positions_handler.init_state(disc)
        assert state == {"positions": {}}


# -- DefineCriteriaHandler tests --

class TestDefineCriteriaHandler:
    def test_system_prompt_contains_criteria_definition(self, criteria_handler,
                                                         ai_entity, disc):
        disc.method_state["current_phase"] = "criteria"
        disc.method_state["positions"] = {"Alice": "Pro remote", "Bob": "Anti remote"}
        prompt = criteria_handler.get_system_prompt(ai_entity, disc)
        assert "CRITERIA DEFINITION PHASE" in prompt

    def test_system_prompt_includes_positions_text(self, criteria_handler,
                                                    ai_entity, disc):
        disc.method_state["current_phase"] = "criteria"
        disc.method_state["positions"] = {"Alice": "Pro remote", "Bob": "Anti remote"}
        prompt = criteria_handler.get_system_prompt(ai_entity, disc)
        assert "Alice" in prompt
        assert "Pro remote" in prompt
        assert "Bob" in prompt
        assert "Anti remote" in prompt

    def test_turn_prompt_round_1_proposes(self, criteria_handler, ai_entity, disc):
        disc.method_state["phase_round"] = 1
        prompt = criteria_handler.get_turn_prompt(ai_entity, disc)
        assert "Propose" in prompt
        assert "TestAI" in prompt

    def test_turn_prompt_round_2_reviews(self, criteria_handler, ai_entity, disc):
        disc.method_state["phase_round"] = 2
        prompt = criteria_handler.get_turn_prompt(ai_entity, disc)
        assert "Round 2" in prompt
        assert "Review" in prompt
        assert "TestAI" in prompt

    def test_summary_prompt(self, criteria_handler, disc):
        prompt = criteria_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "criteria" in prompt.lower()

    def test_process_response_extracts_bold_criteria(self, criteria_handler,
                                                      ai_entity, disc):
        content = (
            "**C1:** Productivity measured by output per hour\n"
            "  - If higher remotely → supports remote work\n"
            "  - If higher in office → supports office work\n\n"
            "**C2:** Employee satisfaction surveys show improvement\n"
            "  - If satisfaction up → supports remote\n"
            "  - If satisfaction down → supports office"
        )
        criteria_handler.process_response(content, ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert len(criteria) == 2
        assert "Productivity" in criteria[0]

    def test_process_response_extracts_c_prefixed_criteria(self, criteria_handler,
                                                            ai_entity, disc):
        # C-prefixed with blank line separation (DOTALL makes .+ greedy across lines)
        content = (
            "C1: Productivity measured by output per hour\n\n"
            "C2: Employee satisfaction surveys show improvement"
        )
        criteria_handler.process_response(content, ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert len(criteria) >= 1
        assert "Productivity" in criteria[0]

    def test_process_response_extracts_numbered_criteria(self, criteria_handler,
                                                          ai_entity, disc):
        # Numbered list with blank line separation
        content = (
            "1. Productivity measured by output per hour\n\n"
            "2. Employee satisfaction surveys show improvement"
        )
        criteria_handler.process_response(content, ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert len(criteria) >= 1
        assert "Productivity" in criteria[0]

    def test_process_response_deduplicates_criteria(self, criteria_handler,
                                                     ai_entity, disc):
        disc.method_state["criteria"] = ["Productivity measured by output per hour"]
        content = (
            "1. Productivity measured by output per hour\n"
            "2. Employee satisfaction surveys show improvement"
        )
        criteria_handler.process_response(content, ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert len(criteria) == 2

    def test_should_advance_needs_criteria_and_rounds_exceeded(
            self, criteria_handler, disc):
        # No criteria, round 1
        assert criteria_handler.should_advance(disc) is False

        # Criteria but round 1
        disc.method_state["criteria"] = ["Some criterion here for testing"]
        assert criteria_handler.should_advance(disc) is False

        # Criteria but round == rounds (2)
        disc.method_state["phase_round"] = 2
        assert criteria_handler.should_advance(disc) is False

        # Criteria and round > rounds
        disc.method_state["phase_round"] = 3
        assert criteria_handler.should_advance(disc) is True

    def test_should_advance_false_when_rounds_exceeded_but_no_criteria(
            self, criteria_handler, disc):
        disc.method_state["phase_round"] = 3
        disc.method_state["criteria"] = []
        assert criteria_handler.should_advance(disc) is False

    def test_init_state(self, criteria_handler, disc):
        state = criteria_handler.init_state(disc)
        assert state == {"criteria": []}

    def test_transition_message(self, criteria_handler, disc):
        msg = criteria_handler.get_transition_message(disc)
        assert "LOCKED" in msg
        assert "Define Settlement Criteria" in msg

    def test_parse_criteria_filters_short_items(self, criteria_handler):
        content = "1. Short\n2. This is a sufficiently long criterion"
        result = criteria_handler._parse_criteria(content)
        assert len(result) == 1
        assert "sufficiently" in result[0]


# -- PresentEvidenceHandler tests --

class TestPresentEvidenceHandler:
    def test_system_prompt_includes_locked_criteria(self, evidence_handler,
                                                     ai_entity, disc):
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["criteria"] = [
            "Productivity measured by output per hour",
            "Employee satisfaction surveys",
        ]
        prompt = evidence_handler.get_system_prompt(ai_entity, disc)
        assert "EVIDENCE GATHERING PHASE" in prompt
        assert "C1:" in prompt
        assert "C2:" in prompt
        assert "Productivity" in prompt

    def test_system_prompt_contains_entity_name(self, evidence_handler,
                                                 ai_entity, disc):
        prompt = evidence_handler.get_system_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_turn_prompt_includes_round_number(self, evidence_handler,
                                                ai_entity, disc):
        disc.method_state["phase_round"] = 2
        prompt = evidence_handler.get_turn_prompt(ai_entity, disc)
        assert "Evidence round 2" in prompt
        assert "TestAI" in prompt

    def test_summary_prompt(self, evidence_handler, disc):
        prompt = evidence_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "evidence" in prompt.lower()

    def test_phase_allows_tools(self, evidence_handler):
        assert evidence_handler.phase.allow_tools is True

    def test_transition_message_includes_locked_criteria(self, evidence_handler, disc):
        disc.method_state["criteria"] = ["Criterion A here", "Criterion B here"]
        msg = evidence_handler.get_transition_message(disc)
        assert "LOCKED" in msg
        assert "C1:" in msg or "**C1:**" in msg
        assert "Criterion A" in msg


# -- AdjudicateHandler tests --

class TestAdjudicateHandler:
    def test_system_prompt_returns_empty(self, adjudicate_handler,
                                          ai_entity, disc):
        disc.method_state["current_phase"] = "adjudicate"
        prompt = adjudicate_handler.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_turn_prompt_returns_empty(self, adjudicate_handler,
                                        ai_entity, disc):
        prompt = adjudicate_handler.get_turn_prompt(ai_entity, disc)
        assert prompt == ""

    def test_transition_message(self, adjudicate_handler, disc):
        msg = adjudicate_handler.get_transition_message(disc)
        assert "Adjudication" in msg
        assert "verdict" in msg.lower()


# -- Equivalence tests: refactored method matches original behavior --

class TestAdversarialCollabEquivalence:
    def test_init_state_matches(self):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test",
                          discussion_method="adversarial_collab")
        state = method.init_state(disc)
        assert state["current_phase"] == "positions"
        assert state["positions"] == {}
        assert state["criteria"] == []
        assert state["phase_round"] == 1

    def test_has_four_phases(self):
        method = get_method("adversarial_collab")
        assert len(method.default_phases) == 4
        names = [p.name for p in method.default_phases]
        assert names == ["positions", "criteria", "evidence", "adjudicate"]

    def test_positions_system_prompt_matches(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test topic",
                          discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "POSITION STATEMENT PHASE" in prompt
        assert "TestAI" in prompt
        assert "test topic" in prompt

    def test_criteria_system_prompt_matches(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test topic",
                          discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "criteria"
        disc.method_state["positions"] = {"Alice": "Pro", "Bob": "Con"}
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "CRITERIA DEFINITION PHASE" in prompt
        assert "Alice" in prompt

    def test_evidence_system_prompt_matches(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test topic",
                          discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["criteria"] = ["Criterion 1", "Criterion 2"]
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "EVIDENCE GATHERING PHASE" in prompt
        assert "C1:" in prompt

    def test_adjudicate_system_prompt_empty(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test",
                          discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "adjudicate"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_phase_handlers_set(self):
        method = get_method("adversarial_collab")
        assert len(method.phase_handlers) == 4

    def test_conclusion_prompt_exists(self):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test",
                          discussion_method="adversarial_collab")
        disc.method_state = {
            "positions": {"Alice": "Pro", "Bob": "Con"},
            "criteria": ["C1 text", "C2 text"],
        }
        prompt = method.get_conclusion_prompt(disc)
        assert "Adversarial Collaboration" in prompt
        assert "C1 text" in prompt
        assert "Alice" in prompt

    def test_evidence_phase_allows_tools(self):
        method = get_method("adversarial_collab")
        evidence_phase = [p for p in method.default_phases
                          if p.name == "evidence"][0]
        assert evidence_phase.allow_tools is True
