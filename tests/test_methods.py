"""Tests for discussion methods — all registered methods."""

import json
import pytest

from consensus.methods import get_method, list_methods
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases._belief_helpers import (
    check_convergence,
    extract_hypotheses_from_framing,
)
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
        assert "premortem" in names
        assert "key_assumptions" in names
        assert "adversarial_collab" in names
        assert "red_team" in names
        assert "delphi" in names
        assert "recursive_decomposition" in names

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
        assert check_convergence(disc) is True

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
        assert check_convergence(disc) is False

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
        hyps = extract_hypotheses_from_framing(content)
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
        from consensus.methods.parsing import word_overlap_similar
        assert word_overlap_similar(
            "The server crashed due to memory issues",
            "The server crashed due to memory problems",
        ) is True
        assert word_overlap_similar(
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


# -- Premortem Analysis tests --

class TestPremortem:
    def test_init_state(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        state = method.init_state(disc)
        assert state["current_phase"] == "frame"
        assert state["conclusion"] == ""

    def test_conclusion_captured_from_frame(self, ai_entity):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)

        content = "We should deploy the new architecture next quarter."
        method.process_response(content, ai_entity, disc)
        assert disc.method_state["conclusion"] == content

    def test_frame_advances_when_conclusion_set(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)

        # No conclusion → don't advance
        assert method.should_advance_phase(disc) is False

        # Conclusion set → advance
        disc.method_state["conclusion"] = "Deploy next quarter"
        assert method.should_advance_phase(disc) is True

    def test_premortem_prompts_include_conclusion(self, ai_entity):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "premortem"
        disc.method_state["conclusion"] = "Switch to microservices"

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "Switch to microservices" in prompt
        assert "FAILED" in prompt

    def test_phase_count(self):
        method = get_method("premortem")
        assert len(method.default_phases) == 3
        assert [p.name for p in method.default_phases] == [
            "frame", "premortem", "consolidate"]

    def test_advance_through_phases(self):
        method = get_method("premortem")
        disc = Discussion(topic="test", discussion_method="premortem")
        disc.method_state = method.init_state(disc)

        disc.method_state["conclusion"] = "Some plan"
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "premortem"

        disc.method_state["phase_round"] = 3  # > rounds=2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "consolidate"


# -- Key Assumptions Check tests --

class TestKeyAssumptions:
    def test_init_state(self):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        state = method.init_state(disc)
        assert state["current_phase"] == "surface"
        assert state["assumptions"] == []

    def test_assumption_extraction(self, ai_entity):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)

        content = (
            "Key assumptions:\n"
            "1. The market will continue to grow at current rates\n"
            "2. Our competitors will not significantly change strategy\n"
            "3. The regulatory environment will remain stable"
        )
        result = method.process_response(content, ai_entity, disc)
        assumptions = disc.method_state["assumptions"]
        assert len(assumptions) == 3
        assert "market" in assumptions[0].lower()

    def test_assumption_dedup(self, ai_entity):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)
        disc.method_state["assumptions"] = [
            "The market will continue to grow at current rates"
        ]

        # Submit a very similar assumption (high word overlap)
        content = "1. The market will continue to grow at current rates steadily"
        method.process_response(content, ai_entity, disc)
        # Should still be 1 assumption (dedup)
        assert len(disc.method_state["assumptions"]) == 1

        # Submit a genuinely different assumption
        content = "1. Competitors will not enter the market segment"
        method.process_response(content, ai_entity, disc)
        assert len(disc.method_state["assumptions"]) == 2

    def test_surface_phase_advances(self):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)

        # No assumptions, round 1 → don't advance
        assert method.should_advance_phase(disc) is False

        # Assumptions set but round 1 → don't advance
        disc.method_state["assumptions"] = ["Some assumption here"]
        assert method.should_advance_phase(disc) is False

        # Assumptions set and round > 1 → advance
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True

    def test_challenge_prompt_lists_assumptions(self, ai_entity):
        method = get_method("key_assumptions")
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "challenge"
        disc.method_state["assumptions"] = [
            "Growth continues", "No competition change"
        ]

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "A1:" in prompt
        assert "A2:" in prompt
        assert "Growth continues" in prompt
        assert "Falsification" in prompt or "falsification" in prompt


# -- Adversarial Collaboration tests --

class TestAdversarialCollab:
    def test_init_state(self):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        state = method.init_state(disc)
        assert state["current_phase"] == "positions"
        assert state["positions"] == {}
        assert state["criteria"] == []

    def test_position_captured_with_entity_name(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)

        content = "I believe the answer is X because of reasons A, B, C."
        method.process_response(content, ai_entity, disc)
        positions = disc.method_state["positions"]
        assert ai_entity.name in positions
        assert "answer is X" in positions[ai_entity.name]

    def test_criteria_extraction(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "criteria"

        content = (
            "**C1:** If studies show >50% improvement, Position A is supported\n"
            "  - If <20% improvement → supports Position B\n\n"
            "**C2:** If cost analysis shows positive ROI within 2 years\n"
            "  - If negative ROI → supports Position B"
        )
        method.process_response(content, ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert len(criteria) >= 2

    def test_positions_phase_advances(self):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)

        assert method.should_advance_phase(disc) is False

        disc.method_state["positions"] = {"Alice": "Position A"}
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True

    def test_criteria_phase_advances(self):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "criteria"

        # No criteria → don't advance
        disc.method_state["phase_round"] = 3
        assert method.should_advance_phase(disc) is False

        # Criteria set + round > 2 → advance
        disc.method_state["criteria"] = ["Some criterion here"]
        assert method.should_advance_phase(disc) is True

    def test_entity_names_in_prompts(self, ai_entity):
        method = get_method("adversarial_collab")
        disc = Discussion(topic="test", discussion_method="adversarial_collab")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "criteria"
        disc.method_state["positions"] = {"Alice": "Position A"}

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "Alice" in prompt
        # Should NOT contain "Entity <id>"
        assert "Entity " not in prompt


# -- Red Team / Blue Team tests --

class TestRedTeam:
    def test_init_state(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        state = method.init_state(disc)
        assert state["current_phase"] == "construct"
        assert state["red_team_entity_id"] is None

    def test_turn_order_excludes_red_team_in_construct(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["red_team_entity_id"] = 1

        order = method.get_turn_order([1, 2, 3], disc)
        assert 1 not in order
        assert order == [2, 3]

    def test_turn_order_red_first_in_attack(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "attack"
        disc.method_state["red_team_entity_id"] = 1

        order = method.get_turn_order([1, 2, 3], disc)
        assert order[0] == 1
        assert order == [1, 2, 3]

    def test_turn_order_excludes_red_team_in_revise(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "revise"
        disc.method_state["red_team_entity_id"] = 2

        order = method.get_turn_order([1, 2, 3], disc)
        assert 2 not in order

    def test_red_team_auto_assigned(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)

        method.get_turn_order([5, 6, 7], disc)
        assert disc.method_state["red_team_entity_id"] == 5

    def test_role_aware_prompts(self, ai_entity):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "attack"
        disc.method_state["red_team_entity_id"] = ai_entity.id

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "RED TEAM" in prompt
        assert "DESTRUCTION" in prompt

    def test_blue_team_prompts(self, ai_entity, human_entity):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "attack"
        disc.method_state["red_team_entity_id"] = ai_entity.id

        # human_entity is Blue Team
        prompt = method.get_system_prompt(human_entity, disc)
        assert "BLUE TEAM" in prompt
        assert "Defend" in prompt or "defend" in prompt


# -- Delphi Method tests --

class TestDelphi:
    def test_init_state(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        state = method.init_state(disc)
        assert state["current_phase"] == "estimate"
        assert state["estimates"] == []
        assert state["revise_round"] == 0

    def test_estimate_extraction_json_block(self, ai_entity):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        content = (
            "My analysis:\n\n"
            "```json\n"
            '{"estimate": 42.5, "confidence": "HIGH", "unit": "percent"}\n'
            "```\n\n"
            "I believe this because..."
        )
        result = method.process_response(content, ai_entity, disc)
        assert result.extracted_data["estimate"] == 42.5
        assert result.extracted_data["confidence"] == "HIGH"
        assert len(disc.method_state["estimates"]) == 1
        assert disc.method_state["estimates"][0]["value"] == 42.5

    def test_estimate_extraction_inline(self, ai_entity):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        content = 'I estimate {"estimate": 0.75, "confidence": "MEDIUM", "unit": "probability"} based on...'
        result = method.process_response(content, ai_entity, disc)
        assert result.extracted_data["estimate"] == 0.75

    def test_display_augmented_with_estimate(self, ai_entity):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        content = '```json\n{"estimate": 100, "confidence": "LOW", "unit": "days"}\n```'
        result = method.process_response(content, ai_entity, disc)
        assert "**Estimate:**" in result.display_content
        assert "100" in result.display_content

    def test_convergence_detection(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)
        disc.method_state["revise_round"] = 2
        disc.method_state["convergence_ratio"] = 0.15

        # Tight cluster → converged
        disc.method_state["estimates"] = [
            {"round": 2, "entity_id": 1, "entity_name": "A",
             "value": 50.0, "confidence": "HIGH"},
            {"round": 2, "entity_id": 2, "entity_name": "B",
             "value": 51.0, "confidence": "HIGH"},
            {"round": 2, "entity_id": 3, "entity_name": "C",
             "value": 50.5, "confidence": "MEDIUM"},
            {"round": 2, "entity_id": 4, "entity_name": "D",
             "value": 49.5, "confidence": "HIGH"},
        ]
        from consensus.methods.phases._delphi_helpers import check_convergence
        assert check_convergence(disc) is True

    def test_no_convergence_with_spread(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)
        disc.method_state["revise_round"] = 1
        disc.method_state["convergence_ratio"] = 0.15

        disc.method_state["estimates"] = [
            {"round": 1, "entity_id": 1, "entity_name": "A",
             "value": 10.0, "confidence": "HIGH"},
            {"round": 1, "entity_id": 2, "entity_name": "B",
             "value": 90.0, "confidence": "LOW"},
            {"round": 1, "entity_id": 3, "entity_name": "C",
             "value": 30.0, "confidence": "MEDIUM"},
            {"round": 1, "entity_id": 4, "entity_name": "D",
             "value": 70.0, "confidence": "MEDIUM"},
        ]
        from consensus.methods.phases._delphi_helpers import check_convergence
        assert check_convergence(disc) is False

    def test_distribution_summary(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)
        disc.method_state["estimates"] = [
            {"round": 0, "entity_id": 1, "entity_name": "A",
             "value": 50.0, "confidence": "HIGH", "unit": "percent"},
            {"round": 0, "entity_id": 2, "entity_name": "B",
             "value": 30.0, "confidence": "LOW", "unit": "percent"},
        ]
        from consensus.methods.phases._delphi_helpers import build_distribution_summary
        summary = build_distribution_summary(disc)
        assert "Mean" in summary
        assert "Median" in summary
        assert "Panelist 1" in summary
        assert "Panelist 2" in summary

    def test_distribution_summary_correct_confidence_with_duplicates(self):
        """Confidence values should be correctly matched even when
        two panelists submit the same numeric estimate."""
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)
        disc.method_state["estimates"] = [
            {"round": 0, "entity_id": 1, "entity_name": "A",
             "value": 50.0, "confidence": "HIGH", "unit": "percent"},
            {"round": 0, "entity_id": 2, "entity_name": "B",
             "value": 50.0, "confidence": "LOW", "unit": "percent"},
        ]
        from consensus.methods.phases._delphi_helpers import build_distribution_summary
        summary = build_distribution_summary(disc)
        # Both should appear with their own confidence
        assert "HIGH" in summary
        assert "LOW" in summary

    def test_estimate_phase_advances(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        assert method.should_advance_phase(disc) is False

        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True

    def test_system_prompt_estimate_phase(self, ai_entity):
        method = get_method("delphi")
        disc = Discussion(topic="How many X?", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "independent" in prompt.lower()
        assert "json" in prompt.lower()
        assert "estimate" in prompt.lower()

    def test_summary_prompt_preserves_anonymity(self):
        method = get_method("delphi")
        disc = Discussion(topic="test", discussion_method="delphi")
        disc.method_state = method.init_state(disc)

        summary = method.get_summary_prompt(disc, "Alice", "Bob")
        # Should not mention Alice by name (anonymity)
        assert "Alice" not in summary
        assert "anonymity" in summary.lower() or "withheld" in summary.lower()
