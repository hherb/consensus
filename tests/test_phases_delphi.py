"""Tests for Delphi Method phase handlers and helpers."""

import pytest

from consensus.methods.delphi import DelphiMethod
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.estimate import EstimateHandler
from consensus.methods.phases.revise_delphi import ReviseDelphiHandler
from consensus.methods.phases.synthesise_delphi import SynthesiseDelphiHandler
from consensus.methods.phases._delphi_helpers import (
    DEFAULT_CONVERGENCE_RATIO,
    MAX_REVISE_ROUNDS,
    extract_estimate,
    build_panelist_map,
    anonymise_content,
    check_convergence,
    build_distribution_summary,
)
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

def _make_discussion(n_participants=3):
    """Create a Delphi discussion with participants."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Expert_{i+1}", entity_type=EntityType.AI, id=i+1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="What is the probability of X?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method="delphi",
    )
    return disc, mod


@pytest.fixture
def method():
    return DelphiMethod()


@pytest.fixture
def discussion(method):
    disc, _ = _make_discussion()
    disc.method_state = method.init_state(disc)
    return disc


@pytest.fixture
def entity():
    return Entity(name="Expert_1", entity_type=EntityType.AI, id=1)


# -- _delphi_helpers tests --

class TestExtractEstimate:
    def test_json_block(self):
        content = '```json\n{"estimate": 0.75, "confidence": "HIGH", "unit": "probability"}\n```\nSome reasoning.'
        result = extract_estimate(content)
        assert result["estimate"] == 0.75
        assert result["confidence"] == "HIGH"
        assert result["unit"] == "probability"

    def test_inline_json(self):
        content = 'I think {"estimate": 42.0, "confidence": "MEDIUM", "unit": "years"} is right.'
        result = extract_estimate(content)
        assert result["estimate"] == 42.0

    def test_natural_language_fallback(self):
        content = "My estimate is 0.85 based on the data."
        result = extract_estimate(content)
        assert result["estimate"] == 0.85
        assert result["confidence"] == ""
        assert result["unit"] == ""

    def test_revised_estimate_natural_language(self):
        content = "My revised estimate is 0.90 after reviewing."
        result = extract_estimate(content)
        assert result["estimate"] == 0.90

    def test_no_estimate_found(self):
        content = "I have no numbers to share."
        result = extract_estimate(content)
        assert result == {}

    def test_invalid_json(self):
        content = '```json\n{broken json}\n```'
        result = extract_estimate(content)
        assert result == {}


class TestBuildPanelistMap:
    def test_creates_mapping(self, discussion):
        pmap = build_panelist_map(discussion)
        assert pmap["Expert_1"] == "Panelist 1"
        assert pmap["Expert_2"] == "Panelist 2"
        assert pmap["Expert_3"] == "Panelist 3"
        assert "Moderator" not in pmap

    def test_caches_in_state(self, discussion):
        pmap1 = build_panelist_map(discussion)
        pmap2 = build_panelist_map(discussion)
        assert pmap1 is pmap2


class TestAnonymiseContent:
    def test_replaces_names(self, discussion):
        content = "Expert_1 said something, Expert_2 agreed."
        result = anonymise_content(content, discussion)
        assert "Expert_1" not in result
        assert "Panelist 1" in result
        assert "Panelist 2" in result


class TestCheckConvergence:
    def test_no_convergence_round_zero(self, discussion):
        assert check_convergence(discussion) is False

    def test_converged(self, discussion):
        discussion.method_state["revise_round"] = 1
        # Very tight cluster -> converged
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.80, "entity_id": 1},
            {"round": 1, "value": 0.81, "entity_id": 2},
            {"round": 1, "value": 0.80, "entity_id": 3},
        ]
        assert check_convergence(discussion) is True

    def test_not_converged(self, discussion):
        discussion.method_state["revise_round"] = 1
        # Wide spread -> not converged
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.10, "entity_id": 1},
            {"round": 1, "value": 0.50, "entity_id": 2},
            {"round": 1, "value": 0.90, "entity_id": 3},
        ]
        assert check_convergence(discussion) is False

    def test_too_few_values(self, discussion):
        discussion.method_state["revise_round"] = 1
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.50, "entity_id": 1},
        ]
        assert check_convergence(discussion) is False


class TestBuildDistributionSummary:
    def test_no_estimates(self, discussion):
        discussion.method_state["estimates"] = []
        assert build_distribution_summary(discussion) == "(No estimates yet)"

    def test_summary_content(self, discussion):
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.7, "confidence": "HIGH", "unit": "prob",
             "entity_id": 1, "entity_name": "Expert_1"},
            {"round": 0, "value": 0.8, "confidence": "MEDIUM", "unit": "prob",
             "entity_id": 2, "entity_name": "Expert_2"},
            {"round": 0, "value": 0.9, "confidence": "LOW", "unit": "prob",
             "entity_id": 3, "entity_name": "Expert_3"},
        ]
        summary = build_distribution_summary(discussion)
        assert "Participants: 3" in summary
        assert "Mean:" in summary
        assert "Median:" in summary
        assert "Panelist 1:" in summary

    def test_summary_labels_match_anonymisation_aliases(self, discussion):
        """Sorted display keeps each panelist's STABLE alias (issue #17)."""
        from consensus.methods.phases._delphi_helpers import (
            build_panelist_map,
        )
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.9, "confidence": "HIGH", "unit": "prob",
             "entity_id": 1, "entity_name": "Expert_1"},
            {"round": 0, "value": 0.2, "confidence": "LOW", "unit": "prob",
             "entity_id": 2, "entity_name": "Expert_2"},
        ]
        aliases = build_panelist_map(discussion)
        summary = build_distribution_summary(discussion)
        assert f"{aliases['Expert_1']}: 0.9" in summary
        assert f"{aliases['Expert_2']}: 0.2" in summary


# -- EstimateHandler tests --

class TestEstimateHandler:
    def test_init_state(self, discussion):
        handler = EstimateHandler()
        state = handler.init_state(discussion)
        assert state["estimates"] == []
        assert state["revise_round"] == 0
        assert state["max_revise_rounds"] == MAX_REVISE_ROUNDS
        assert state["convergence_ratio"] == DEFAULT_CONVERGENCE_RATIO

    def test_system_prompt(self, entity, discussion):
        handler = EstimateHandler()
        prompt = handler.get_system_prompt(entity, discussion)
        assert "INITIAL ESTIMATE PHASE" in prompt
        assert entity.name in prompt
        assert discussion.topic in prompt

    def test_turn_prompt(self, entity, discussion):
        handler = EstimateHandler()
        prompt = handler.get_turn_prompt(entity, discussion)
        assert entity.name in prompt
        assert "JSON code block" in prompt

    def test_summary_prompt(self, discussion):
        handler = EstimateHandler()
        prompt = handler.get_summary_prompt(discussion, "Speaker", "NextSpeaker")
        assert "Do NOT reveal" in prompt
        assert "NextSpeaker" in prompt

    def test_filter_context_message_anonymises(self, discussion):
        handler = EstimateHandler()
        content = "Expert_1 thinks the answer is 42."
        result = handler.filter_context_message("Expert_1", content, "user", discussion)
        assert "Expert_1" not in result
        assert "Panelist 1" in result

    def test_process_response_extracts_estimate(self, entity, discussion):
        handler = EstimateHandler()
        content = '```json\n{"estimate": 0.75, "confidence": "HIGH", "unit": "prob"}\n```\nMy reasoning.'
        result = handler.process_response(content, entity, discussion)
        assert "**Estimate:** 0.75" in result.display_content
        # Check stored in state
        estimates = discussion.method_state["estimates"]
        assert len(estimates) == 1
        assert estimates[0]["round"] == 0
        assert estimates[0]["value"] == 0.75

    def test_process_response_no_estimate(self, entity, discussion):
        handler = EstimateHandler()
        content = "I have no numeric answer."
        result = handler.process_response(content, entity, discussion)
        assert result.display_content == content
        assert not discussion.method_state.get("estimates")

    def test_should_advance_after_round(self, discussion):
        handler = EstimateHandler()
        discussion.method_state["phase_round"] = 1
        assert handler.should_advance(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert handler.should_advance(discussion) is True


# -- ReviseDelphiHandler tests --

class TestReviseDelphiHandler:
    def test_system_prompt_includes_summary(self, entity, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.7, "confidence": "HIGH", "unit": "prob", "entity_id": 1},
            {"round": 0, "value": 0.8, "confidence": "MEDIUM", "unit": "prob", "entity_id": 2},
        ]
        prompt = handler.get_system_prompt(entity, discussion)
        assert "REVISION ROUND" in prompt
        assert "Participants:" in prompt

    def test_turn_prompt(self, entity, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["revise_round"] = 1
        prompt = handler.get_turn_prompt(entity, discussion)
        assert "Revision round 2" in prompt

    def test_filter_context_message_anonymises(self, discussion):
        handler = ReviseDelphiHandler()
        content = "Expert_2 changed their mind."
        result = handler.filter_context_message("Expert_2", content, "user", discussion)
        assert "Expert_2" not in result
        assert "Panelist 2" in result

    def test_process_response_stores_with_correct_round(self, entity, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["revise_round"] = 2
        content = '```json\n{"estimate": 0.80, "confidence": "HIGH", "unit": "prob"}\n```\nRevised.'
        result = handler.process_response(content, entity, discussion)
        assert "0.80" in result.display_content
        stored = discussion.method_state["estimates"][-1]
        assert stored["value"] == 0.80
        assert stored["round"] == 3  # revise_round + 1

    def test_should_advance_on_convergence(self, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["revise_round"] = 1
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.80, "entity_id": 1},
            {"round": 1, "value": 0.81, "entity_id": 2},
            {"round": 1, "value": 0.80, "entity_id": 3},
        ]
        assert handler.should_advance(discussion) is True

    def test_should_advance_on_max_rounds(self, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["revise_round"] = MAX_REVISE_ROUNDS
        assert handler.should_advance(discussion) is True

    def test_should_not_advance_early(self, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["revise_round"] = 1
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.10, "entity_id": 1},
            {"round": 1, "value": 0.90, "entity_id": 2},
            {"round": 1, "value": 0.50, "entity_id": 3},
        ]
        assert handler.should_advance(discussion) is False

    def test_transition_message(self, discussion):
        handler = ReviseDelphiHandler()
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.7, "confidence": "HIGH", "unit": "prob", "entity_id": 1},
        ]
        msg = handler.get_transition_message(discussion)
        assert "Revision Rounds" in msg
        assert "anonymised" in msg


# -- SynthesiseDelphiHandler tests --

class TestSynthesiseDelphiHandler:
    def test_system_prompt_empty(self, entity, discussion):
        handler = SynthesiseDelphiHandler()
        assert handler.get_system_prompt(entity, discussion) == ""

    def test_turn_prompt_empty(self, entity, discussion):
        handler = SynthesiseDelphiHandler()
        assert handler.get_turn_prompt(entity, discussion) == ""

    def test_filter_context_message_does_not_anonymise(self, discussion):
        handler = SynthesiseDelphiHandler()
        content = "Expert_1 had the best estimate."
        result = handler.filter_context_message("Expert_1", content, "user", discussion)
        assert result == content
        assert "Expert_1" in result

    def test_should_advance_after_round(self, discussion):
        handler = SynthesiseDelphiHandler()
        discussion.method_state["phase_round"] = 1
        assert handler.should_advance(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert handler.should_advance(discussion) is True

    def test_transition_message_converged(self, discussion):
        handler = SynthesiseDelphiHandler()
        discussion.method_state["revise_round"] = 1
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.80, "entity_id": 1},
            {"round": 1, "value": 0.81, "entity_id": 2},
            {"round": 1, "value": 0.80, "entity_id": 3},
        ]
        msg = handler.get_transition_message(discussion)
        assert "converged" in msg

    def test_transition_message_round_limit(self, discussion):
        handler = SynthesiseDelphiHandler()
        # No convergence
        discussion.method_state["revise_round"] = 0
        msg = handler.get_transition_message(discussion)
        assert "reached the round limit" in msg


# -- DelphiMethod integration / equivalence tests --

class TestDelphiMethodEquivalence:
    """Test that the refactored method produces identical behavior."""

    def test_init_state(self, method, discussion):
        state = discussion.method_state
        assert state["current_phase"] == "estimate"
        assert state["estimates"] == []
        assert state["revise_round"] == 0
        assert state["max_revise_rounds"] == MAX_REVISE_ROUNDS
        assert state["convergence_ratio"] == DEFAULT_CONVERGENCE_RATIO

    def test_phases_auto_derived(self, method):
        assert len(method.default_phases) == 3
        assert method.default_phases[0].name == "estimate"
        assert method.default_phases[1].name == "revise"
        assert method.default_phases[2].name == "synthesise"

    def test_estimate_system_prompt(self, method, entity, discussion):
        prompt = method.get_system_prompt(entity, discussion)
        assert "INITIAL ESTIMATE PHASE" in prompt
        assert entity.name in prompt

    def test_revise_system_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "revise"
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.7, "confidence": "HIGH", "unit": "prob", "entity_id": 1},
        ]
        prompt = method.get_system_prompt(entity, discussion)
        assert "REVISION ROUND" in prompt

    def test_synthesise_system_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "synthesise"
        prompt = method.get_system_prompt(entity, discussion)
        assert prompt == ""

    def test_estimate_turn_prompt(self, method, entity, discussion):
        prompt = method.get_turn_prompt(entity, discussion)
        assert entity.name in prompt
        assert "CRITICAL" in prompt

    def test_revise_turn_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "revise"
        prompt = method.get_turn_prompt(entity, discussion)
        assert "Revision round" in prompt

    def test_synthesise_turn_prompt(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "synthesise"
        prompt = method.get_turn_prompt(entity, discussion)
        assert prompt == ""

    def test_estimate_summary_prompt(self, method, discussion):
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Do NOT reveal" in prompt
        assert "Bob" in prompt

    def test_revise_summary_prompt(self, method, discussion):
        discussion.method_state["current_phase"] = "revise"
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert "anonymity must be preserved" in prompt

    def test_filter_context_anonymises_in_estimate(self, method, discussion):
        content = "Expert_1 said something."
        result = method.filter_context_message("Expert_1", content, "user", discussion)
        assert "Expert_1" not in result
        assert "Panelist 1" in result

    def test_filter_context_anonymises_in_revise(self, method, discussion):
        discussion.method_state["current_phase"] = "revise"
        content = "Expert_2 changed their mind."
        result = method.filter_context_message("Expert_2", content, "user", discussion)
        assert "Expert_2" not in result

    def test_filter_context_reveals_in_synthesise(self, method, discussion):
        discussion.method_state["current_phase"] = "synthesise"
        content = "Expert_1 had the best estimate."
        result = method.filter_context_message("Expert_1", content, "user", discussion)
        assert "Expert_1" in result

    def test_process_response_estimate(self, method, entity, discussion):
        content = '```json\n{"estimate": 0.75, "confidence": "HIGH", "unit": "prob"}\n```\nReasoning.'
        result = method.process_response(content, entity, discussion)
        assert "**Estimate:** 0.75" in result.display_content
        assert discussion.method_state["estimates"][0]["round"] == 0

    def test_process_response_revise(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "revise"
        discussion.method_state["revise_round"] = 1
        content = '```json\n{"estimate": 0.80, "confidence": "HIGH", "unit": "prob"}\n```\nRevised.'
        result = method.process_response(content, entity, discussion)
        assert discussion.method_state["estimates"][0]["round"] == 2

    def test_process_response_synthesise_passthrough(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "synthesise"
        content = "Final synthesis text."
        result = method.process_response(content, entity, discussion)
        assert result.display_content == content

    def test_should_advance_estimate(self, method, discussion):
        assert method.should_advance_phase(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_revise_convergence(self, method, discussion):
        discussion.method_state["current_phase"] = "revise"
        discussion.method_state["revise_round"] = 1
        discussion.method_state["estimates"] = [
            {"round": 1, "value": 0.80, "entity_id": 1},
            {"round": 1, "value": 0.81, "entity_id": 2},
            {"round": 1, "value": 0.80, "entity_id": 3},
        ]
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_revise_max_rounds(self, method, discussion):
        discussion.method_state["current_phase"] = "revise"
        discussion.method_state["revise_round"] = MAX_REVISE_ROUNDS
        assert method.should_advance_phase(discussion) is True

    def test_should_advance_synthesise(self, method, discussion):
        discussion.method_state["current_phase"] = "synthesise"
        discussion.method_state["phase_round"] = 1
        assert method.should_advance_phase(discussion) is False
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True

    def test_on_round_complete_revise(self, method, discussion):
        discussion.method_state["current_phase"] = "revise"
        discussion.method_state["revise_round"] = 0
        method.on_round_complete(discussion)
        assert discussion.method_state["revise_round"] == 1
        assert discussion.method_state["phase_round"] == 2

    def test_on_round_complete_estimate(self, method, discussion):
        method.on_round_complete(discussion)
        # revise_round should NOT change outside revise phase
        assert discussion.method_state["revise_round"] == 0
        assert discussion.method_state["phase_round"] == 2

    def test_get_conclusion_prompt(self, method, discussion):
        discussion.method_state["estimates"] = [
            {"round": 0, "entity_id": 1, "entity_name": "Expert_1",
             "value": 0.7, "confidence": "HIGH"},
        ]
        prompt = method.get_conclusion_prompt(discussion)
        assert "Delphi Method process is complete" in prompt
        assert "Expert_1" in prompt

    def test_phase_transition_to_revise(self, method, discussion):
        discussion.method_state["estimates"] = [
            {"round": 0, "value": 0.7, "confidence": "HIGH", "unit": "prob", "entity_id": 1},
        ]
        revise_phase = method.default_phases[1]
        msg = method.get_phase_transition_message(revise_phase, discussion)
        assert "Revision Rounds" in msg

    def test_phase_transition_to_synthesise(self, method, discussion):
        synth_phase = method.default_phases[2]
        msg = method.get_phase_transition_message(synth_phase, discussion)
        assert "Synthesis" in msg
