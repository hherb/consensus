"""Tests for ACH (Analysis of Competing Hypotheses) phase handlers.

Tests each handler in isolation AND verifies the refactored method
produces identical behavior to the original monolithic implementation.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.hypothesize import HypothesizeHandler
from consensus.methods.phases.gather_evidence import GatherEvidenceHandler
from consensus.methods.phases.evaluate_matrix import EvaluateMatrixHandler
from consensus.methods.phases.analyse_ach import AnalyseACHHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def ai_entity2():
    return Entity(name="TestAI2", entity_type=EntityType.AI, id=2)


@pytest.fixture
def hypothesize_handler():
    return HypothesizeHandler()


@pytest.fixture
def evidence_handler():
    return GatherEvidenceHandler()


@pytest.fixture
def evaluate_handler():
    return EvaluateMatrixHandler()


@pytest.fixture
def analyse_handler():
    return AnalyseACHHandler()


@pytest.fixture
def disc():
    method = get_method("ach")
    d = Discussion(topic="Why did the Roman Empire fall?",
                   discussion_method="ach")
    d.method_state = method.init_state(d)
    return d


# -- HypothesizeHandler tests --

class TestHypothesizeHandler:
    def test_system_prompt_contains_hypothesis_generation(
            self, hypothesize_handler, ai_entity, disc):
        prompt = hypothesize_handler.get_system_prompt(ai_entity, disc)
        assert "HYPOTHESIS GENERATION PHASE" in prompt

    def test_system_prompt_contains_entity_name(
            self, hypothesize_handler, ai_entity, disc):
        prompt = hypothesize_handler.get_system_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_system_prompt_contains_topic(
            self, hypothesize_handler, ai_entity, disc):
        prompt = hypothesize_handler.get_system_prompt(ai_entity, disc)
        assert "Why did the Roman Empire fall?" in prompt

    def test_turn_prompt_contains_entity_name(
            self, hypothesize_handler, ai_entity, disc):
        prompt = hypothesize_handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_process_response_extracts_hypotheses(
            self, hypothesize_handler, ai_entity, disc):
        content = (
            "Here are my hypotheses:\n"
            "1. Economic decline and hyperinflation weakened the empire\n"
            "2. Military overextension and inability to defend borders\n"
            "3. Internal political corruption and civil wars"
        )
        result = hypothesize_handler.process_response(content, ai_entity, disc)
        hypotheses = disc.method_state["hypotheses"]
        assert len(hypotheses) == 3
        assert "Economic decline" in hypotheses[0]
        assert isinstance(result, ProcessedResponse)
        assert result.extracted_data.get("new_hypotheses")

    def test_process_response_deduplicates(
            self, hypothesize_handler, ai_entity, disc):
        disc.method_state["hypotheses"] = [
            "Economic decline and hyperinflation weakened the empire"
        ]
        # Near-duplicate should not be added
        content = "1. Economic decline and hyperinflation weakened the empire significantly"
        hypothesize_handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["hypotheses"]) == 1

        # Different hypothesis should be added
        content = "1. Barbarian invasions overwhelmed the frontier defenses"
        hypothesize_handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["hypotheses"]) == 2

    def test_should_advance_requires_hypotheses_and_round_gt_1(
            self, hypothesize_handler, disc):
        # No hypotheses, round 1
        assert hypothesize_handler.should_advance(disc) is False

        # Hypotheses but round 1
        disc.method_state["hypotheses"] = ["Some hypothesis here"]
        assert hypothesize_handler.should_advance(disc) is False

        # Hypotheses and round > 1
        disc.method_state["phase_round"] = 2
        assert hypothesize_handler.should_advance(disc) is True

    def test_should_advance_false_when_round_gt_1_but_no_hypotheses(
            self, hypothesize_handler, disc):
        disc.method_state["phase_round"] = 2
        disc.method_state["hypotheses"] = []
        assert hypothesize_handler.should_advance(disc) is False

    def test_init_state(self, hypothesize_handler, disc):
        state = hypothesize_handler.init_state(disc)
        assert state == {
            "hypotheses": [],
            "evidence": [],
            "matrix": {},
            "next_evidence_id": 1,
        }

    def test_summary_prompt(self, hypothesize_handler, disc):
        prompt = hypothesize_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "hypotheses" in prompt.lower()

    def test_filters_short_hypotheses(
            self, hypothesize_handler, ai_entity, disc):
        content = (
            "1. Short\n"
            "2. This is a sufficiently long hypothesis text\n"
        )
        hypothesize_handler.process_response(content, ai_entity, disc)
        hypotheses = disc.method_state["hypotheses"]
        assert len(hypotheses) == 1
        assert "sufficiently long" in hypotheses[0]


# -- GatherEvidenceHandler tests --

class TestGatherEvidenceHandler:
    def test_system_prompt_contains_evidence_gathering(
            self, evidence_handler, ai_entity, disc):
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["hypotheses"] = ["Economic decline", "Military overextension"]
        prompt = evidence_handler.get_system_prompt(ai_entity, disc)
        assert "EVIDENCE GATHERING PHASE" in prompt
        assert "H1:" in prompt
        assert "H2:" in prompt

    def test_system_prompt_contains_hypotheses(
            self, evidence_handler, ai_entity, disc):
        disc.method_state["hypotheses"] = ["Hypothesis Alpha", "Hypothesis Beta"]
        prompt = evidence_handler.get_system_prompt(ai_entity, disc)
        assert "Hypothesis Alpha" in prompt
        assert "Hypothesis Beta" in prompt

    def test_turn_prompt_contains_round_number(
            self, evidence_handler, ai_entity, disc):
        disc.method_state["phase_round"] = 2
        prompt = evidence_handler.get_turn_prompt(ai_entity, disc)
        assert "round 2" in prompt

    def test_process_response_extracts_bold_evidence(
            self, evidence_handler, ai_entity, disc):
        content = (
            "**E1:** GDP per capita declined 40% between 200-400 AD (Source: Maddison Project)\n"
            "  Supports: H1 | Contradicts: H2\n"
            "**E2:** Roman legions were reduced from 33 to 20 by 400 AD (Source: Notitia Dignitatum)\n"
            "  Supports: H2 | Contradicts: H1\n"
        )
        result = evidence_handler.process_response(content, ai_entity, disc)
        evidence = disc.method_state["evidence"]
        assert len(evidence) == 2
        assert evidence[0]["id"] == 1
        assert evidence[1]["id"] == 2
        assert evidence[0]["contributor"] == "TestAI"
        assert evidence[0]["contributor_id"] == 1
        assert "Maddison" in evidence[0]["source"]
        assert result.extracted_data["evidence_count"] == 2

    def test_process_response_extracts_numbered_fallback(
            self, evidence_handler, ai_entity, disc):
        content = (
            "1. The debasement of Roman coinage reduced silver content from 95% to 5% (Source: Numismatic studies)\n"
            "2. The Antonine Plague killed an estimated 5 million people (Source: Ancient records)\n"
        )
        result = evidence_handler.process_response(content, ai_entity, disc)
        evidence = disc.method_state["evidence"]
        assert len(evidence) == 2
        assert evidence[0]["id"] == 1
        assert result.extracted_data["evidence_count"] == 2

    def test_process_response_assigns_auto_increment_ids(
            self, evidence_handler, ai_entity, ai_entity2, disc):
        content1 = "**E1:** First evidence item text here (Source: Source1)\n"
        evidence_handler.process_response(content1, ai_entity, disc)

        content2 = "**E1:** Second evidence item text here (Source: Source2)\n"
        evidence_handler.process_response(content2, ai_entity2, disc)

        evidence = disc.method_state["evidence"]
        assert evidence[0]["id"] == 1
        assert evidence[1]["id"] == 2
        assert evidence[1]["contributor"] == "TestAI2"

    def test_process_response_warns_on_no_evidence(
            self, evidence_handler, ai_entity, disc, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            evidence_handler.process_response("No evidence here.", ai_entity, disc)
        assert "Could not extract evidence" in caplog.text

    def test_transition_message_lists_hypotheses(
            self, evidence_handler, disc):
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        msg = evidence_handler.get_transition_message(disc)
        assert "Hyp A" in msg
        assert "Hyp B" in msg
        assert "DISPROVES" in msg

    def test_summary_prompt(self, evidence_handler, disc):
        prompt = evidence_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


# -- EvaluateMatrixHandler tests --

class TestEvaluateMatrixHandler:
    def test_system_prompt_contains_matrix_evaluation(
            self, evaluate_handler, ai_entity, disc):
        disc.method_state["current_phase"] = "evaluate"
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Evidence 1", "source": "Source 1"},
        ]
        prompt = evaluate_handler.get_system_prompt(ai_entity, disc)
        assert "MATRIX EVALUATION PHASE" in prompt
        assert "H1:" in prompt
        assert "E1:" in prompt

    def test_system_prompt_contains_rating_instructions(
            self, evaluate_handler, ai_entity, disc):
        disc.method_state["hypotheses"] = ["Hyp A"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
        ]
        prompt = evaluate_handler.get_system_prompt(ai_entity, disc)
        assert "+ (consistent)" in prompt
        assert "- (inconsistent)" in prompt
        assert "0 (neutral)" in prompt

    def test_process_response_parses_json_ratings(
            self, evaluate_handler, ai_entity, disc):
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
            {"id": 2, "text": "Ev2", "source": "S2"},
        ]
        content = (
            'Here are my ratings:\n'
            '```json\n'
            '{"ratings": {"H1": {"E1": "+", "E2": "-"}, '
            '"H2": {"E1": "0", "E2": "+"}}}\n'
            '```\n'
            'H1/E2 is inconsistent because...'
        )
        result = evaluate_handler.process_response(content, ai_entity, disc)
        assert disc.method_state["matrix"]["1"]["H1"]["E1"] == "+"
        assert disc.method_state["matrix"]["1"]["H1"]["E2"] == "-"
        assert "Rating Matrix" in result.display_content
        assert result.extracted_data["ratings"] is not None

    def test_process_response_parses_unfenced_json_block(
            self, evaluate_handler, ai_entity, disc):
        """Inline JSON without fenced code block -- uses fallback regex."""
        disc.method_state["hypotheses"] = ["Hyp A"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
        ]
        # Fenced block without the json language hint
        content = (
            'My ratings:\n'
            '```\n'
            '{"ratings": {"H1": {"E1": "+"}}}\n'
            '```'
        )
        result = evaluate_handler.process_response(content, ai_entity, disc)
        assert disc.method_state["matrix"]["1"]["H1"]["E1"] == "+"

    def test_process_response_warns_on_bad_json(
            self, evaluate_handler, ai_entity, disc, caplog):
        import logging
        disc.method_state["hypotheses"] = ["Hyp A"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
        ]
        with caplog.at_level(logging.WARNING):
            evaluate_handler.process_response("No JSON here.", ai_entity, disc)
        assert "Could not extract ratings" in caplog.text

    def test_format_rating_matrix(self, evaluate_handler, disc):
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
            {"id": 2, "text": "Ev2", "source": "S2"},
        ]
        ratings = {"H1": {"E1": "+", "E2": "-"}, "H2": {"E1": "0", "E2": "+"}}
        text = evaluate_handler._format_rating_matrix(ratings, disc)
        assert "**Rating Matrix:**" in text
        assert "E1" in text
        assert "E2" in text
        assert "**H1**" in text

    def test_should_advance_after_round_1(self, evaluate_handler, disc):
        disc.method_state["phase_round"] = 1
        assert evaluate_handler.should_advance(disc) is False

        disc.method_state["phase_round"] = 2
        assert evaluate_handler.should_advance(disc) is True

    def test_transition_message_mentions_evidence_count(
            self, evaluate_handler, disc):
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1"},
            {"id": 2, "text": "Ev2"},
            {"id": 3, "text": "Ev3"},
        ]
        msg = evaluate_handler.get_transition_message(disc)
        assert "3" in msg

    def test_summary_prompt(self, evaluate_handler, disc):
        prompt = evaluate_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


# -- AnalyseACHHandler tests --

class TestAnalyseACHHandler:
    def test_system_prompt_returns_empty(
            self, analyse_handler, ai_entity, disc):
        prompt = analyse_handler.get_system_prompt(ai_entity, disc)
        assert prompt == ""

    def test_turn_prompt_returns_empty(
            self, analyse_handler, ai_entity, disc):
        prompt = analyse_handler.get_turn_prompt(ai_entity, disc)
        assert prompt == ""

    def test_should_advance_after_round_1(self, analyse_handler, disc):
        disc.method_state["phase_round"] = 1
        assert analyse_handler.should_advance(disc) is False

        disc.method_state["phase_round"] = 2
        assert analyse_handler.should_advance(disc) is True

    def test_transition_message(self, analyse_handler, disc):
        msg = analyse_handler.get_transition_message(disc)
        assert "moderator" in msg.lower() or "analyse" in msg.lower()


# -- Equivalence tests: refactored method matches original behavior --

class TestACHEquivalence:
    def test_init_state_matches(self):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        state = method.init_state(disc)
        assert state["current_phase"] == "hypothesize"
        assert state["hypotheses"] == []
        assert state["evidence"] == []
        assert state["matrix"] == {}
        assert state["next_evidence_id"] == 1
        assert state["phase_round"] == 1

    def test_has_four_phases(self):
        method = get_method("ach")
        assert len(method.default_phases) == 4
        names = [p.name for p in method.default_phases]
        assert names == ["hypothesize", "evidence", "evaluate", "analyse"]

    def test_phase_handlers_set(self):
        method = get_method("ach")
        assert len(method.phase_handlers) == 4

    def test_hypothesize_system_prompt_via_method(self):
        method = get_method("ach")
        entity = Entity(name="Alice", entity_type=EntityType.AI, id=1)
        disc = Discussion(topic="Test topic", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        prompt = method.get_system_prompt(entity, disc)
        assert "HYPOTHESIS GENERATION PHASE" in prompt
        assert "Alice" in prompt
        assert "Test topic" in prompt

    def test_evidence_system_prompt_via_method(self):
        method = get_method("ach")
        entity = Entity(name="Bob", entity_type=EntityType.AI, id=2)
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["hypotheses"] = ["Hypothesis One", "Hypothesis Two"]
        prompt = method.get_system_prompt(entity, disc)
        assert "EVIDENCE GATHERING PHASE" in prompt
        assert "H1:" in prompt
        assert "Hypothesis One" in prompt

    def test_evaluate_system_prompt_via_method(self):
        method = get_method("ach")
        entity = Entity(name="Carol", entity_type=EntityType.AI, id=3)
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "evaluate"
        disc.method_state["hypotheses"] = ["H1 text"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1", "source": "S1"},
        ]
        prompt = method.get_system_prompt(entity, disc)
        assert "MATRIX EVALUATION PHASE" in prompt

    def test_analyse_system_prompt_via_method(self):
        method = get_method("ach")
        entity = Entity(name="Dave", entity_type=EntityType.AI, id=4)
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "analyse"
        prompt = method.get_system_prompt(entity, disc)
        assert prompt == ""

    def test_conclusion_prompt(self):
        method = get_method("ach")
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Ev1"},
            {"id": 2, "text": "Ev2"},
        ]
        disc.method_state["matrix"] = {
            "1": {"H1": {"E1": "+", "E2": "-"}, "H2": {"E1": "-", "E2": "+"}},
        }
        prompt = method.get_conclusion_prompt(disc)
        assert "ACH evaluation is complete" in prompt
        assert "Hypothesis ranking" in prompt
        assert "Diagnostic evidence" in prompt
        assert "Sensitivity analysis" in prompt

    def test_process_response_delegates_to_handler(self):
        method = get_method("ach")
        entity = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        content = "1. First hypothesis that is long enough\n2. Second hypothesis that is also long"
        result = method.process_response(content, entity, disc)
        assert isinstance(result, ProcessedResponse)
        assert len(disc.method_state["hypotheses"]) == 2

    def test_should_advance_phase_delegates(self):
        method = get_method("ach")
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        assert method.should_advance_phase(disc) is False

        disc.method_state["hypotheses"] = ["Some hypothesis text"]
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True

    def test_phase_transition_messages(self):
        method = get_method("ach")
        disc = Discussion(topic="Test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["hypotheses"] = ["HA", "HB"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "E1"},
            {"id": 2, "text": "E2"},
        ]

        evidence_phase = method.default_phases[1]
        msg = method.get_phase_transition_message(evidence_phase, disc)
        assert "DISPROVES" in msg

        evaluate_phase = method.default_phases[2]
        msg = method.get_phase_transition_message(evaluate_phase, disc)
        assert "2 pieces of evidence" in msg

        analyse_phase = method.default_phases[3]
        msg = method.get_phase_transition_message(analyse_phase, disc)
        assert "moderator" in msg.lower()

    def test_evidence_allow_tools(self):
        method = get_method("ach")
        evidence_phase = method.default_phases[1]
        assert evidence_phase.allow_tools is True
