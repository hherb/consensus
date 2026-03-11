"""Tests for Belief Diffusion phase handlers and helpers."""

import pytest

from consensus.methods.belief_diffusion import BeliefDiffusion
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.frame_hypotheses import FrameHypothesesHandler
from consensus.methods.phases.prior_beliefs import PriorBeliefsHandler
from consensus.methods.phases.diffuse_beliefs import DiffuseBeliefsHandler
from consensus.methods.phases.diagnose_beliefs import DiagnoseHandler
from consensus.methods.phases._belief_helpers import (
    DEFAULT_CONVERGENCE_THRESHOLD,
    MAX_DIFFUSE_ROUNDS,
    BELIEF_BAR_WIDTH,
    MIN_HYPOTHESIS_LENGTH,
    extract_beliefs,
    format_belief_bar,
    format_others_beliefs,
    build_trajectory_summary,
    extract_hypotheses_from_framing,
    check_convergence,
)
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

def _make_discussion(n_participants=3):
    """Create a Belief Diffusion discussion with participants."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Analyst_{i+1}", entity_type=EntityType.AI, id=i + 1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="Will AI surpass human reasoning by 2030?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method="belief_diffusion",
    )
    return disc, mod


@pytest.fixture
def method():
    return BeliefDiffusion()


@pytest.fixture
def discussion(method):
    disc, _ = _make_discussion()
    disc.method_state = method.init_state(disc)
    return disc


@pytest.fixture
def entity():
    return Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)


@pytest.fixture
def entity2():
    return Entity(name="Analyst_2", entity_type=EntityType.AI, id=2)


# -- _belief_helpers tests --

class TestExtractBeliefs:
    def test_json_block(self):
        content = '```json\n{"beliefs": {"H1": 0.6, "H2": 0.3, "H3": 0.1}}\n```\nSome reasoning.'
        result = extract_beliefs(content)
        assert result == {"H1": 0.6, "H2": 0.3, "H3": 0.1}

    def test_json_block_no_lang_tag(self):
        content = '```\n{"beliefs": {"H1": 0.5, "H2": 0.5}}\n```'
        result = extract_beliefs(content)
        assert result == {"H1": 0.5, "H2": 0.5}

    def test_inline_json(self):
        content = 'My beliefs are {"beliefs": {"H1": 0.7, "H2": 0.3}} based on evidence.'
        result = extract_beliefs(content)
        assert result == {"H1": 0.7, "H2": 0.3}

    def test_missing_beliefs_key(self):
        content = '```json\n{"estimate": 0.5}\n```'
        result = extract_beliefs(content)
        assert result == {}

    def test_no_json(self):
        content = "I think hypothesis 1 is most likely but I have no numbers."
        result = extract_beliefs(content)
        assert result == {}

    def test_invalid_json(self):
        content = '```json\n{broken json}\n```'
        result = extract_beliefs(content)
        assert result == {}

    def test_float_conversion(self):
        content = '```json\n{"beliefs": {"H1": "0.6", "H2": "0.4"}}\n```'
        result = extract_beliefs(content)
        assert result == {"H1": 0.6, "H2": 0.4}


class TestFormatBeliefBar:
    def test_basic_format(self, discussion):
        discussion.method_state["hypotheses"] = ["AI will surpass", "AI won't surpass"]
        beliefs = {"H1": 0.7, "H2": 0.3}
        result = format_belief_bar(beliefs, discussion)
        assert "**Belief Distribution:**" in result
        assert "H1 (70%)" in result
        assert "H2 (30%)" in result
        assert "AI will surpass" in result

    def test_unknown_hypothesis_key(self, discussion):
        discussion.method_state["hypotheses"] = ["Something"]
        beliefs = {"X1": 0.5}
        result = format_belief_bar(beliefs, discussion)
        # Falls back to key label
        assert "X1" in result


class TestFormatOthersBeliefs:
    def test_no_history(self, entity, discussion):
        discussion.method_state["belief_history"] = []
        result = format_others_beliefs(entity, discussion)
        assert "No beliefs recorded yet" in result

    def test_excludes_self(self, entity, discussion):
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "entity_name": "Analyst_1",
             "beliefs": {"H1": 0.5, "H2": 0.5}, "round": 0},
        ]
        result = format_others_beliefs(entity, discussion)
        assert "No other participants" in result

    def test_shows_others(self, entity, discussion):
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "entity_name": "Analyst_1",
             "beliefs": {"H1": 0.5, "H2": 0.5}, "round": 0},
            {"entity_id": 2, "entity_name": "Analyst_2",
             "beliefs": {"H1": 0.7, "H2": 0.3}, "round": 0},
        ]
        result = format_others_beliefs(entity, discussion)
        assert "Analyst_2" in result
        assert "Analyst_1" not in result


class TestBuildTrajectorySummary:
    def test_no_data(self, discussion):
        discussion.method_state["belief_history"] = []
        assert build_trajectory_summary(discussion) == "(No data)"

    def test_trajectory(self, discussion):
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "entity_name": "Analyst_1",
             "beliefs": {"H1": 0.5, "H2": 0.5}, "round": 0},
            {"entity_id": 1, "entity_name": "Analyst_1",
             "beliefs": {"H1": 0.7, "H2": 0.3}, "round": 1},
        ]
        result = build_trajectory_summary(discussion)
        assert "**Analyst_1:**" in result
        assert "Prior:" in result
        assert "Round 1:" in result


class TestExtractHypothesesFromFraming:
    def test_numbered_list(self):
        content = "1. AI will surpass by 2028\n2. AI will surpass by 2032\n3. AI will never surpass"
        result = extract_hypotheses_from_framing(content)
        assert len(result) == 3
        assert "AI will surpass by 2028" in result

    def test_h_numbered(self):
        content = "H1: First hypothesis here\nH2: Second hypothesis here"
        result = extract_hypotheses_from_framing(content)
        assert len(result) == 2

    def test_bullet_list(self):
        content = "- AI surpasses in narrow tasks\n- AI surpasses in general reasoning"
        result = extract_hypotheses_from_framing(content)
        assert len(result) == 2

    def test_short_items_filtered(self):
        content = "1. Yes\n2. No\n3. A proper hypothesis with detail"
        result = extract_hypotheses_from_framing(content)
        # "Yes" and "No" are < MIN_HYPOTHESIS_LENGTH
        assert len(result) == 1

    def test_empty_content(self):
        result = extract_hypotheses_from_framing("")
        assert result == []


class TestCheckConvergence:
    def test_no_history(self, discussion):
        assert check_convergence(discussion) is False

    def test_round_too_low(self, discussion):
        discussion.method_state["diffuse_round"] = 1
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.5}, "round": 1},
        ]
        assert check_convergence(discussion) is False

    def test_converged(self, discussion):
        discussion.method_state["diffuse_round"] = 2
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.80, "H2": 0.20}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.75, "H2": 0.25}, "round": 1},
            {"entity_id": 1, "beliefs": {"H1": 0.81, "H2": 0.19}, "round": 2},
            {"entity_id": 2, "beliefs": {"H1": 0.76, "H2": 0.24}, "round": 2},
        ]
        assert check_convergence(discussion) is True

    def test_not_converged(self, discussion):
        discussion.method_state["diffuse_round"] = 2
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.50, "H2": 0.50}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.60, "H2": 0.40}, "round": 1},
            {"entity_id": 1, "beliefs": {"H1": 0.90, "H2": 0.10}, "round": 2},
            {"entity_id": 2, "beliefs": {"H1": 0.30, "H2": 0.70}, "round": 2},
        ]
        assert check_convergence(discussion) is False

    def test_no_current_beliefs(self, discussion):
        discussion.method_state["diffuse_round"] = 3
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.50}, "round": 1},
        ]
        assert check_convergence(discussion) is False


# -- FrameHypothesesHandler tests --

class TestFrameHypothesesHandler:
    def test_init_state(self, discussion):
        handler = FrameHypothesesHandler()
        state = handler.init_state(discussion)
        assert state["hypotheses"] == []
        assert state["belief_history"] == []
        assert state["convergence_threshold"] == DEFAULT_CONVERGENCE_THRESHOLD
        assert state["max_diffuse_rounds"] == MAX_DIFFUSE_ROUNDS
        assert state["diffuse_round"] == 0

    def test_system_prompt_empty(self, entity, discussion):
        handler = FrameHypothesesHandler()
        assert handler.get_system_prompt(entity, discussion) == ""

    def test_turn_prompt_empty(self, entity, discussion):
        handler = FrameHypothesesHandler()
        assert handler.get_turn_prompt(entity, discussion) == ""

    def test_should_advance_no_hypotheses(self, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["hypotheses"] = []
        assert handler.should_advance(discussion) is False

    def test_should_advance_with_hypotheses(self, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["hypotheses"] = ["H1", "H2"]
        assert handler.should_advance(discussion) is True

    def test_process_response_extracts_hypotheses(self, entity, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["hypotheses"] = []
        content = (
            "I've identified these hypotheses:\n"
            "1. Climate change is primarily human-caused\n"
            "2. Climate change is primarily natural cycles\n"
            "3. Climate change is a mix of both factors\n"
        )
        result = handler.process_response(content, entity, discussion)
        assert len(discussion.method_state["hypotheses"]) == 3
        assert "human-caused" in discussion.method_state["hypotheses"][0]
        assert result.display_content == content

    def test_process_response_no_hypotheses_found(self, entity, discussion):
        handler = FrameHypothesesHandler()
        discussion.method_state["hypotheses"] = []
        content = "Let me think about this topic..."
        result = handler.process_response(content, entity, discussion)
        assert discussion.method_state["hypotheses"] == []
        assert result.display_content == content


# -- PriorBeliefsHandler tests --

class TestPriorBeliefsHandler:
    def test_system_prompt_includes_hypotheses(self, entity, discussion):
        handler = PriorBeliefsHandler()
        discussion.method_state["hypotheses"] = [
            "AI surpasses by 2028",
            "AI surpasses by 2032",
        ]
        prompt = handler.get_system_prompt(entity, discussion)
        assert entity.name in prompt
        assert discussion.topic in prompt
        assert "AI surpasses by 2028" in prompt
        assert "AI surpasses by 2032" in prompt
        assert "INITIAL probability distribution" in prompt
        assert "json" in prompt.lower()

    def test_turn_prompt(self, entity, discussion):
        handler = PriorBeliefsHandler()
        prompt = handler.get_turn_prompt(entity, discussion)
        assert entity.name in prompt
        assert "initial beliefs" in prompt.lower()

    def test_summary_prompt(self, discussion):
        handler = PriorBeliefsHandler()
        prompt = handler.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "initial beliefs" in prompt.lower()

    def test_process_response_extracts_beliefs(self, entity, discussion):
        handler = PriorBeliefsHandler()
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        content = '```json\n{"beliefs": {"H1": 0.6, "H2": 0.4}}\n```\nMy reasoning.'
        result = handler.process_response(content, entity, discussion)
        assert "**Belief Distribution:**" in result.display_content
        assert result.extracted_data == {"beliefs": {"H1": 0.6, "H2": 0.4}}
        # Check stored in history
        history = discussion.method_state["belief_history"]
        assert len(history) == 1
        assert history[0]["round"] == 0
        assert history[0]["entity_id"] == entity.id

    def test_process_response_no_beliefs(self, entity, discussion):
        handler = PriorBeliefsHandler()
        content = "I have no structured beliefs to share."
        result = handler.process_response(content, entity, discussion)
        assert result.display_content == content
        assert result.extracted_data == {}

    def test_should_advance_before_round(self, discussion):
        handler = PriorBeliefsHandler()
        discussion.method_state["phase_round"] = 1
        assert handler.should_advance(discussion) is False

    def test_should_advance_after_round(self, discussion):
        handler = PriorBeliefsHandler()
        discussion.method_state["phase_round"] = 2
        assert handler.should_advance(discussion) is True

    def test_transition_message(self, discussion):
        handler = PriorBeliefsHandler()
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        msg = handler.get_transition_message(discussion)
        assert "Prior Beliefs" in msg
        assert "**H1:**" in msg
        assert "Hyp A" in msg
        assert "sum to 1.0" in msg


# -- DiffuseBeliefsHandler tests --

class TestDiffuseBeliefsHandler:
    def test_system_prompt_includes_others_beliefs(self, entity, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        discussion.method_state["belief_history"] = [
            {"entity_id": 2, "entity_name": "Analyst_2",
             "beliefs": {"H1": 0.7, "H2": 0.3}, "round": 0},
        ]
        prompt = handler.get_system_prompt(entity, discussion)
        assert "Analyst_2" in prompt
        assert "UPDATED probability distribution" in prompt

    def test_turn_prompt_shows_round_number(self, entity, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["diffuse_round"] = 2
        prompt = handler.get_turn_prompt(entity, discussion)
        assert "Diffusion round 3" in prompt
        assert entity.name in prompt

    def test_summary_prompt(self, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["diffuse_round"] = 1
        prompt = handler.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
        assert "direction and magnitude" in prompt

    def test_process_response_records_correct_round(self, entity, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        discussion.method_state["diffuse_round"] = 2
        content = '```json\n{"beliefs": {"H1": 0.8, "H2": 0.2}}\n```\nUpdated.'
        result = handler.process_response(content, entity, discussion)
        history = discussion.method_state["belief_history"]
        assert len(history) == 1
        assert history[0]["round"] == 3  # diffuse_round + 1

    def test_should_advance_on_convergence(self, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["diffuse_round"] = 2
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.80}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.79}, "round": 1},
            {"entity_id": 1, "beliefs": {"H1": 0.81}, "round": 2},
            {"entity_id": 2, "beliefs": {"H1": 0.80}, "round": 2},
        ]
        assert handler.should_advance(discussion) is True

    def test_should_advance_on_round_limit(self, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["diffuse_round"] = MAX_DIFFUSE_ROUNDS
        assert handler.should_advance(discussion) is True

    def test_should_not_advance_early(self, discussion):
        handler = DiffuseBeliefsHandler()
        discussion.method_state["diffuse_round"] = 1
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.30}, "round": 0},
            {"entity_id": 2, "beliefs": {"H1": 0.90}, "round": 0},
            {"entity_id": 1, "beliefs": {"H1": 0.50}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.70}, "round": 1},
        ]
        assert handler.should_advance(discussion) is False

    def test_transition_message(self, discussion):
        handler = DiffuseBeliefsHandler()
        msg = handler.get_transition_message(discussion)
        assert "Belief Diffusion" in msg
        assert "converge" in msg


# -- DiagnoseHandler tests --

class TestDiagnoseHandler:
    def test_system_prompt_empty(self, entity, discussion):
        handler = DiagnoseHandler()
        assert handler.get_system_prompt(entity, discussion) == ""

    def test_turn_prompt_empty(self, entity, discussion):
        handler = DiagnoseHandler()
        assert handler.get_turn_prompt(entity, discussion) == ""

    def test_should_advance_before_round(self, discussion):
        handler = DiagnoseHandler()
        discussion.method_state["phase_round"] = 1
        assert handler.should_advance(discussion) is False

    def test_should_advance_after_round(self, discussion):
        handler = DiagnoseHandler()
        discussion.method_state["phase_round"] = 2
        assert handler.should_advance(discussion) is True

    def test_transition_message_converged(self, discussion):
        handler = DiagnoseHandler()
        discussion.method_state["diffuse_round"] = 2
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.80}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.79}, "round": 1},
            {"entity_id": 1, "beliefs": {"H1": 0.81}, "round": 2},
            {"entity_id": 2, "beliefs": {"H1": 0.80}, "round": 2},
        ]
        msg = handler.get_transition_message(discussion)
        assert "converged" in msg

    def test_transition_message_round_limit(self, discussion):
        handler = DiagnoseHandler()
        discussion.method_state["diffuse_round"] = 0
        msg = handler.get_transition_message(discussion)
        assert "reached the round limit" in msg


# -- BeliefDiffusion integration tests --

class TestBeliefDiffusionIntegration:
    """Test the refactored method produces identical behavior."""

    def test_phases_auto_derived(self, method):
        assert len(method.default_phases) == 4
        assert method.default_phases[0].name == "frame"
        assert method.default_phases[1].name == "prior"
        assert method.default_phases[2].name == "diffuse"
        assert method.default_phases[3].name == "diagnose"

    def test_init_state(self, method, discussion):
        state = discussion.method_state
        assert state["current_phase"] == "frame"
        assert state["hypotheses"] == []
        assert state["belief_history"] == []
        assert state["diffuse_round"] == 0
        assert state["convergence_threshold"] == DEFAULT_CONVERGENCE_THRESHOLD
        assert state["max_diffuse_rounds"] == MAX_DIFFUSE_ROUNDS

    def test_frame_system_prompt_empty(self, method, entity, discussion):
        prompt = method.get_system_prompt(entity, discussion)
        assert prompt == ""

    def test_frame_turn_prompt_empty(self, method, entity, discussion):
        prompt = method.get_turn_prompt(entity, discussion)
        assert prompt == ""

    def test_prior_system_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "prior"
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        prompt = method.get_system_prompt(entity, discussion)
        assert "INITIAL probability distribution" in prompt
        assert "Hyp A" in prompt

    def test_prior_turn_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "prior"
        prompt = method.get_turn_prompt(entity, discussion)
        assert entity.name in prompt

    def test_diffuse_system_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["hypotheses"] = ["Hyp A"]
        discussion.method_state["belief_history"] = [
            {"entity_id": 2, "entity_name": "Analyst_2",
             "beliefs": {"H1": 0.7}, "round": 0},
        ]
        prompt = method.get_system_prompt(entity, discussion)
        assert "UPDATED probability distribution" in prompt
        assert "Analyst_2" in prompt

    def test_diffuse_turn_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["diffuse_round"] = 0
        prompt = method.get_turn_prompt(entity, discussion)
        assert "Diffusion round 1" in prompt

    def test_diagnose_system_prompt_empty(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "diagnose"
        prompt = method.get_system_prompt(entity, discussion)
        assert prompt == ""

    def test_process_response_prior(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "prior"
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        content = '```json\n{"beliefs": {"H1": 0.6, "H2": 0.4}}\n```\nReasoning.'
        result = method.process_response(content, entity, discussion)
        assert "**Belief Distribution:**" in result.display_content
        assert discussion.method_state["belief_history"][0]["round"] == 0

    def test_process_response_diffuse(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        discussion.method_state["diffuse_round"] = 1
        content = '```json\n{"beliefs": {"H1": 0.8, "H2": 0.2}}\n```\nUpdated.'
        result = method.process_response(content, entity, discussion)
        assert discussion.method_state["belief_history"][0]["round"] == 2

    def test_process_response_diagnose_passthrough(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "diagnose"
        content = "Diagnosis text."
        result = method.process_response(content, entity, discussion)
        assert result.display_content == content

    def test_should_advance_frame(self, method, discussion):
        assert method.should_advance_phase(discussion) is False
        discussion.method_state["hypotheses"] = ["H1", "H2"]
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_prior(self, method, discussion):
        discussion.method_state["current_phase"] = "prior"
        discussion.method_state["phase_round"] = 1
        assert method.should_advance_phase(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_diffuse_convergence(self, method, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["diffuse_round"] = 2
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "beliefs": {"H1": 0.80}, "round": 1},
            {"entity_id": 2, "beliefs": {"H1": 0.79}, "round": 1},
            {"entity_id": 1, "beliefs": {"H1": 0.81}, "round": 2},
            {"entity_id": 2, "beliefs": {"H1": 0.80}, "round": 2},
        ]
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_diffuse_max_rounds(self, method, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["diffuse_round"] = MAX_DIFFUSE_ROUNDS
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_diagnose(self, method, discussion):
        discussion.method_state["current_phase"] = "diagnose"
        discussion.method_state["phase_round"] = 1
        assert method.should_advance_phase(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True

    def test_on_round_complete_diffuse(self, method, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        discussion.method_state["diffuse_round"] = 0
        method.on_round_complete(discussion)
        assert discussion.method_state["diffuse_round"] == 1
        assert discussion.method_state["phase_round"] == 2

    def test_on_round_complete_non_diffuse(self, method, discussion):
        method.on_round_complete(discussion)
        assert discussion.method_state["diffuse_round"] == 0
        assert discussion.method_state["phase_round"] == 2

    def test_get_conclusion_prompt(self, method, discussion):
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        discussion.method_state["belief_history"] = [
            {"entity_id": 1, "entity_name": "Analyst_1",
             "beliefs": {"H1": 0.6, "H2": 0.4}, "round": 0},
        ]
        prompt = method.get_conclusion_prompt(discussion)
        assert "Belief State Diffusion process is complete" in prompt
        assert "Hyp A" in prompt
        assert "Analyst_1" in prompt
        assert "Convergence analysis" in prompt

    def test_phase_transition_to_prior(self, method, discussion):
        discussion.method_state["hypotheses"] = ["Hyp A", "Hyp B"]
        prior_phase = method.default_phases[1]
        msg = method.get_phase_transition_message(prior_phase, discussion)
        assert "Prior Beliefs" in msg
        assert "Hyp A" in msg

    def test_phase_transition_to_diffuse(self, method, discussion):
        diffuse_phase = method.default_phases[2]
        msg = method.get_phase_transition_message(diffuse_phase, discussion)
        assert "Belief Diffusion" in msg

    def test_phase_transition_to_diagnose(self, method, discussion):
        diagnose_phase = method.default_phases[3]
        msg = method.get_phase_transition_message(diagnose_phase, discussion)
        assert "Diagnosis" in msg

    def test_summary_prompt_prior(self, method, discussion):
        discussion.method_state["current_phase"] = "prior"
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt

    def test_summary_prompt_diffuse(self, method, discussion):
        discussion.method_state["current_phase"] = "diffuse"
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
