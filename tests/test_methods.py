"""Tests for discussion methods — base, open_discussion, belief_diffusion, ach."""

import json
import pytest

from consensus.methods import get_method, list_methods
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def ai_entity():
    """A simple AI entity for testing."""
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def human_entity():
    """A simple human entity for testing."""
    return Entity(name="TestHuman", entity_type=EntityType.HUMAN, id=2)


# -- Registry tests --

class TestRegistry:
    def test_list_methods_returns_all(self):
        methods = list_methods()
        names = [m["name"] for m in methods]
        assert "open_discussion" in names
        assert "belief_diffusion" in names
        assert "ach" in names

    def test_get_method_known(self):
        method = get_method("ach")
        assert method.name == "ach"

    def test_get_method_unknown_raises(self):
        with pytest.raises(KeyError):
            get_method("nonexistent_method")

    def test_method_has_phases(self):
        for info in list_methods():
            assert "phases" in info
            assert len(info["phases"]) > 0


# -- OpenDiscussion tests --

class TestOpenDiscussion:
    def test_returns_empty_prompts(self, ai_entity):
        method = get_method("open_discussion")
        disc = Discussion(topic="test", discussion_method="open_discussion")
        assert method.get_system_prompt(ai_entity, disc) == ""
        assert method.get_turn_prompt(ai_entity, disc) == ""

    def test_single_phase(self):
        method = get_method("open_discussion")
        assert len(method.default_phases) == 1
        assert method.default_phases[0].rounds == 0  # unlimited


# -- Belief State Diffusion tests --

class TestBeliefDiffusion:
    def test_init_state(self):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        state = method.init_state(disc)
        assert state["current_phase"] == "frame"
        assert state["hypotheses"] == []
        assert state["belief_history"] == []

    def test_extract_beliefs_from_json_block(self, ai_entity):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "prior"
        disc.method_state["hypotheses"] = ["Hyp A", "Hyp B"]

        content = (
            "My analysis:\n\n"
            "```json\n"
            '{"beliefs": {"H1": 0.7, "H2": 0.3}}\n'
            "```\n\n"
            "I favour H1 because..."
        )
        result = method.process_response(content, ai_entity, disc)
        assert result.extracted_data["beliefs"] == {"H1": 0.7, "H2": 0.3}
        assert len(disc.method_state["belief_history"]) == 1

    def test_extract_beliefs_inline_json(self, ai_entity):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "diffuse"
        disc.method_state["hypotheses"] = ["A", "B"]

        content = 'Updated: {"beliefs": {"H1": 0.5, "H2": 0.5}} because...'
        result = method.process_response(content, ai_entity, disc)
        assert result.extracted_data["beliefs"] == {"H1": 0.5, "H2": 0.5}

    def test_belief_bar_in_display(self, ai_entity):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "prior"
        disc.method_state["hypotheses"] = ["Yes", "No"]

        content = '```json\n{"beliefs": {"H1": 0.8, "H2": 0.2}}\n```'
        result = method.process_response(content, ai_entity, disc)
        assert "█" in result.display_content
        assert "80%" in result.display_content

    def test_convergence_detection(self):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "diffuse"
        disc.method_state["diffuse_round"] = 3
        disc.method_state["convergence_threshold"] = 0.05
        disc.method_state["belief_history"] = [
            {"round": 2, "entity_id": 1, "entity_name": "A",
             "beliefs": {"H1": 0.7, "H2": 0.3}},
            {"round": 3, "entity_id": 1, "entity_name": "A",
             "beliefs": {"H1": 0.72, "H2": 0.28}},
        ]
        assert method._check_convergence(disc) is True

    def test_no_convergence_with_large_shift(self):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "diffuse"
        disc.method_state["diffuse_round"] = 3
        disc.method_state["belief_history"] = [
            {"round": 2, "entity_id": 1, "entity_name": "A",
             "beliefs": {"H1": 0.7, "H2": 0.3}},
            {"round": 3, "entity_id": 1, "entity_name": "A",
             "beliefs": {"H1": 0.4, "H2": 0.6}},
        ]
        assert method._check_convergence(disc) is False

    def test_phase_system_prompts(self, ai_entity):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="Is AI safe?",
                          discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)
        disc.method_state["hypotheses"] = ["Safe", "Dangerous"]

        # Frame phase — empty (moderator handles)
        disc.method_state["current_phase"] = "frame"
        assert method.get_system_prompt(ai_entity, disc) == ""

        # Prior phase — instructs belief output
        disc.method_state["current_phase"] = "prior"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "probability distribution" in prompt.lower()
        assert "json" in prompt.lower()

        # Diffuse phase — shows others' beliefs
        disc.method_state["current_phase"] = "diffuse"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "updated" in prompt.lower()

    def test_hypothesis_extraction_from_framing(self):
        method = get_method("belief_diffusion")
        content = (
            "I've identified the following hypotheses:\n"
            "1. Climate change is primarily human-caused\n"
            "2. Climate change is primarily natural\n"
            "3. Climate change is a combination of both factors"
        )
        hyps = method.extract_hypotheses_from_framing(content)
        assert len(hyps) == 3
        assert "human-caused" in hyps[0].lower()


# -- ACH tests --

class TestACH:
    def test_init_state(self):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        state = method.init_state(disc)
        assert state["current_phase"] == "hypothesize"
        assert state["evidence"] == []
        assert state["matrix"] == {}

    def test_hypothesis_extraction(self, ai_entity):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        disc.method_state = method.init_state(disc)

        content = (
            "My hypotheses:\n"
            "1. The server crash was caused by a memory leak in the cache\n"
            "2. The crash was triggered by a spike in traffic exceeding capacity\n"
            "3. A misconfigured deployment caused the outage"
        )
        result = method.process_response(content, ai_entity, disc)
        hyps = disc.method_state["hypotheses"]
        assert len(hyps) == 3
        assert "memory leak" in hyps[0].lower()

    def test_evidence_extraction(self, ai_entity):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["hypotheses"] = ["Memory leak", "Traffic spike"]

        content = (
            "**E1:** Server memory usage grew linearly over 48h "
            "(Source: CloudWatch metrics)\n"
            "  Supports: H1 | Contradicts: H2\n\n"
            "**E2:** Traffic was 3x normal at time of crash "
            "(Source: load balancer logs)\n"
            "  Supports: H2"
        )
        result = method.process_response(content, ai_entity, disc)
        evidence = disc.method_state["evidence"]
        assert len(evidence) == 2
        assert evidence[0]["id"] == 1
        assert evidence[1]["id"] == 2
        assert "CloudWatch" in evidence[0]["source"]

    def test_rating_matrix_extraction(self, ai_entity):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "evaluate"
        disc.method_state["hypotheses"] = ["Memory leak", "Traffic spike"]
        disc.method_state["evidence"] = [
            {"id": 1, "text": "Memory grew", "source": "CW"},
            {"id": 2, "text": "Traffic 3x", "source": "LB"},
        ]

        content = (
            '```json\n'
            '{"ratings": {"H1": {"E1": "+", "E2": "0"}, '
            '"H2": {"E1": "-", "E2": "+"}}}\n'
            '```\n'
            "H1 is consistent with E1 but..."
        )
        result = method.process_response(content, ai_entity, disc)
        matrix = disc.method_state["matrix"]
        assert str(ai_entity.id) in matrix
        ratings = matrix[str(ai_entity.id)]
        assert ratings["H1"]["E1"] == "+"
        assert ratings["H2"]["E1"] == "-"

    def test_phase_prompts(self, ai_entity):
        method = get_method("ach")
        disc = Discussion(topic="Why did X fail?", discussion_method="ach")
        disc.method_state = method.init_state(disc)

        # Hypothesize phase
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "hypothesis" in prompt.lower()
        assert "2-3" in prompt

        # Evidence phase
        disc.method_state["current_phase"] = "evidence"
        disc.method_state["hypotheses"] = ["H1 text", "H2 text"]
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "evidence" in prompt.lower()
        assert "disconfirming" in prompt.lower()

        # Evaluate phase
        disc.method_state["current_phase"] = "evaluate"
        disc.method_state["evidence"] = [
            {"id": 1, "text": "ev1", "source": "s1"},
        ]
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "matrix" in prompt.lower() or "rating" in prompt.lower()

    def test_similar_hypothesis_dedup(self):
        method = get_method("ach")
        assert method._similar(
            "The server crashed due to memory issues",
            "The server crashed due to memory problems",
        ) is True
        assert method._similar(
            "The server crashed due to memory issues",
            "A network outage caused the downtime",
        ) is False

    def test_aggregate_matrix(self):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        disc.method_state = {
            "hypotheses": ["H1 text", "H2 text"],
            "evidence": [
                {"id": 1, "text": "ev1", "source": "s1"},
                {"id": 2, "text": "ev2", "source": "s2"},
            ],
            "matrix": {
                "1": {"H1": {"E1": "+", "E2": "-"},
                      "H2": {"E1": "-", "E2": "+"}},
                "2": {"H1": {"E1": "+", "E2": "-"},
                      "H2": {"E1": "0", "E2": "+"}},
            },
        }
        agg = method._aggregate_matrix(disc)
        assert "inconsistencies" in agg.lower()
        assert "H1" in agg
        assert "H2" in agg


# -- Phase transition tests --

class TestPhaseTransitions:
    def test_advance_phase_belief_diffusion(self):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)

        # Set hypotheses and advance from frame to prior
        disc.method_state["hypotheses"] = ["A", "B"]
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase is not None
        assert new_phase.name == "prior"
        assert disc.method_state["current_phase"] == "prior"

    def test_advance_phase_ach(self):
        method = get_method("ach")
        disc = Discussion(topic="test", discussion_method="ach")
        disc.method_state = method.init_state(disc)

        # Hypothesize → Evidence (need hypotheses + round > 1)
        disc.method_state["hypotheses"] = ["H1", "H2"]
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "evidence"

    def test_all_phases_exhausted(self):
        method = get_method("belief_diffusion")
        disc = Discussion(topic="test", discussion_method="belief_diffusion")
        disc.method_state = method.init_state(disc)

        # Walk through all phases
        disc.method_state["current_phase"] = "diagnose"
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        result = method.advance_phase(disc)
        assert result is None  # all phases done
