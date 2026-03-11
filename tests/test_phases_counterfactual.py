"""Tests for Counterfactual Stress Testing phase handlers and helpers."""

import pytest

from consensus.methods.phases._counterfactual_helpers import (
    extract_impact_score,
    classify_claim,
    format_results_table,
)


class TestExtractImpactScore:
    def test_standard_tag(self):
        content = "The conclusion falls apart entirely. [IMPACT: 5]"
        assert extract_impact_score(content) == 5

    def test_low_impact(self):
        content = "Not much changes. [IMPACT: 1]"
        assert extract_impact_score(content) == 1

    def test_mid_impact(self):
        content = "Some elements weaken. [IMPACT: 3]"
        assert extract_impact_score(content) == 3

    def test_no_tag(self):
        content = "I think the impact is moderate."
        assert extract_impact_score(content) is None

    def test_tag_in_middle(self):
        content = "Analysis here. [IMPACT: 4] More text after."
        assert extract_impact_score(content) == 4

    def test_out_of_range_high(self):
        content = "[IMPACT: 7]"
        assert extract_impact_score(content) is None

    def test_out_of_range_zero(self):
        content = "[IMPACT: 0]"
        assert extract_impact_score(content) is None

    def test_whitespace_variations(self):
        content = "[IMPACT:  3 ]"
        assert extract_impact_score(content) == 3

    def test_lowercase_ignored(self):
        content = "[impact: 3]"
        assert extract_impact_score(content) is None


class TestClassifyClaim:
    def test_load_bearing(self):
        assert classify_claim(4.5) == "LOAD-BEARING"

    def test_load_bearing_threshold(self):
        assert classify_claim(4.0) == "LOAD-BEARING"

    def test_supportive(self):
        assert classify_claim(3.0) == "SUPPORTIVE"

    def test_supportive_threshold(self):
        assert classify_claim(2.0) == "SUPPORTIVE"

    def test_decorative(self):
        assert classify_claim(1.5) == "DECORATIVE"

    def test_decorative_low(self):
        assert classify_claim(1.0) == "DECORATIVE"


class TestFormatResultsTable:
    def test_basic_table(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Claim one text",
                "scores": {"Alice": 5, "Bob": 4},
                "avg_score": 4.5,
                "classification": "LOAD-BEARING",
            },
            {
                "claim_id": 2,
                "claim_text": "Claim two text",
                "scores": {"Alice": 1, "Bob": 2},
                "avg_score": 1.5,
                "classification": "DECORATIVE",
            },
        ]
        table = format_results_table(results)
        assert "Claim one text" in table
        assert "4.5" in table
        assert "LOAD-BEARING" in table
        assert "DECORATIVE" in table

    def test_empty_results(self):
        table = format_results_table([])
        assert "No claims" in table or table == ""

    def test_none_scores(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Untested claim",
                "scores": {},
                "avg_score": None,
                "classification": None,
            },
        ]
        table = format_results_table(results)
        assert "Untested claim" in table


from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.counterfactual_deliberate import CounterfactualDeliberateHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

def _make_discussion(n_participants=3):
    """Create a counterfactual discussion with participants."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Analyst_{i+1}", entity_type=EntityType.AI, id=i + 1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="Should cities ban personal car ownership?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method="counterfactual",
    )
    return disc, mod


@pytest.fixture
def deliberate_handler():
    return CounterfactualDeliberateHandler()


@pytest.fixture
def cf_discussion():
    disc, _ = _make_discussion()
    disc.method_state = {
        "current_phase": "cf_deliberate",
        "phase_round": 1,
        "preliminary_conclusion": None,
        "prior_conclusion": None,
        "claims": [],
        "claim_results": [],
        "current_claim_index": 0,
        "extraction_failed": False,
        "extraction_attempts": 0,
    }
    return disc


@pytest.fixture
def entity():
    return Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)


class TestCounterfactualDeliberateHandler:
    def test_phase_metadata(self, deliberate_handler):
        assert deliberate_handler.phase.name == "cf_deliberate"
        assert deliberate_handler.phase.rounds == 2
        assert deliberate_handler.phase.allow_tools is True

    def test_init_state(self, deliberate_handler, cf_discussion):
        state = deliberate_handler.init_state(cf_discussion)
        assert state["preliminary_conclusion"] is None
        assert state["prior_conclusion"] is None

    def test_system_prompt_includes_topic(self, deliberate_handler, entity, cf_discussion):
        prompt = deliberate_handler.get_system_prompt(entity, cf_discussion)
        assert entity.name in prompt
        assert cf_discussion.topic in prompt
        assert "preliminary conclusion" in prompt.lower()

    def test_turn_prompt(self, deliberate_handler, entity, cf_discussion):
        prompt = deliberate_handler.get_turn_prompt(entity, cf_discussion)
        assert entity.name in prompt

    def test_should_advance_default(self, deliberate_handler, cf_discussion):
        cf_discussion.method_state["phase_round"] = 1
        assert deliberate_handler.should_advance(cf_discussion) is False
        cf_discussion.method_state["phase_round"] = 3
        assert deliberate_handler.should_advance(cf_discussion) is True

    def test_transition_message(self, deliberate_handler, cf_discussion):
        msg = deliberate_handler.get_transition_message(cf_discussion)
        assert "Deliberation" in msg


from consensus.methods.phases.counterfactual_extract import ExtractClaimsHandler


class TestExtractClaimsHandler:
    @pytest.fixture
    def handler(self):
        return ExtractClaimsHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "extract"
        assert handler.phase.rounds == 0
        assert handler.phase.allow_tools is False

    def test_init_state(self, handler, cf_discussion):
        state = handler.init_state(cf_discussion)
        assert state["claims"] == []
        assert state["claim_results"] == []
        assert state["current_claim_index"] == 0
        assert state["extraction_failed"] is False
        assert state["extraction_attempts"] == 0

    def test_turn_order_moderator_only(self, handler, cf_discussion):
        entity_ids = [1, 2, 3]
        result = handler.get_turn_order(entity_ids, cf_discussion)
        assert result == [cf_discussion.moderator_id]

    def test_turn_prompt_includes_conclusion(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "Cars should be banned" in prompt
        assert "3-7" in prompt
        assert "numbered" in prompt.lower()

    def test_turn_prompt_uses_prior_conclusion(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["prior_conclusion"] = "AI will surpass humans."
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "AI will surpass humans" in prompt

    def test_turn_prompt_retry(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["preliminary_conclusion"] = "Some conclusion."
        cf_discussion.method_state["extraction_failed"] = True
        cf_discussion.method_state["extraction_attempts"] = 1
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "failed" in prompt.lower() or "try again" in prompt.lower()
        assert "numbered" in prompt.lower()

    def test_process_response_extracts_claims(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        content = (
            "Key claims:\n"
            "1. Personal cars contribute significantly to urban pollution\n"
            "2. Public transit can fully replace personal car usage\n"
            "3. Car bans would reduce traffic fatalities substantially\n"
        )
        result = handler.process_response(content, entity, cf_discussion)
        claims = cf_discussion.method_state["claims"]
        assert len(claims) == 3
        assert claims[0]["id"] == 1
        assert "urban pollution" in claims[0]["text"]
        assert len(cf_discussion.method_state["claim_results"]) == 3
        assert cf_discussion.method_state["extraction_failed"] is False

    def test_process_response_no_claims_sets_failed(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        content = "I think there are many factors to consider."
        result = handler.process_response(content, entity, cf_discussion)
        assert cf_discussion.method_state["extraction_failed"] is True
        assert cf_discussion.method_state["extraction_attempts"] == 1
        assert cf_discussion.method_state["claims"] == []

    def test_should_advance_with_claims(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = [{"id": 1, "text": "A claim"}]
        assert handler.should_advance(cf_discussion) is True

    def test_should_advance_no_claims_no_advance(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = []
        cf_discussion.method_state["extraction_attempts"] = 1
        assert handler.should_advance(cf_discussion) is False

    def test_should_advance_gives_up_after_3(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = []
        cf_discussion.method_state["extraction_attempts"] = 3
        assert handler.should_advance(cf_discussion) is True

    def test_process_response_retry_then_success_clears_failed(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["extraction_failed"] = True
        cf_discussion.method_state["extraction_attempts"] = 1
        content = "1. Cars cause significant urban pollution\n2. Public transit is viable\n"
        handler.process_response(content, entity, cf_discussion)
        assert cf_discussion.method_state["extraction_failed"] is False
        assert len(cf_discussion.method_state["claims"]) == 2


from consensus.methods.phases.counterfactual_stress import StressTestHandler


class TestStressTestHandler:
    @pytest.fixture
    def handler(self):
        return StressTestHandler()

    @pytest.fixture
    def stress_discussion(self, cf_discussion):
        cf_discussion.method_state["current_phase"] = "stress_test"
        cf_discussion.method_state["claims"] = [
            {"id": 1, "text": "Cars cause significant urban pollution"},
            {"id": 2, "text": "Public transit can replace personal cars"},
            {"id": 3, "text": "Car bans reduce traffic fatalities"},
        ]
        cf_discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Cars cause significant urban pollution",
             "scores": {}, "avg_score": None, "classification": None},
            {"claim_id": 2, "claim_text": "Public transit can replace personal cars",
             "scores": {}, "avg_score": None, "classification": None},
            {"claim_id": 3, "claim_text": "Car bans reduce traffic fatalities",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        cf_discussion.method_state["current_claim_index"] = 0
        cf_discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        return cf_discussion

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "stress_test"
        assert handler.phase.rounds == 0
        assert handler.phase.allow_tools is True

    def test_system_prompt_includes_claim(self, handler, entity, stress_discussion):
        prompt = handler.get_system_prompt(entity, stress_discussion)
        assert "Cars cause significant urban pollution" in prompt
        assert "FALSE" in prompt
        assert "must argue" in prompt.lower()

    def test_system_prompt_changes_with_index(self, handler, entity, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        prompt = handler.get_system_prompt(entity, stress_discussion)
        assert "Public transit can replace personal cars" in prompt

    def test_turn_prompt_includes_claim_and_impact_tag(self, handler, entity, stress_discussion):
        prompt = handler.get_turn_prompt(entity, stress_discussion)
        assert "Cars cause significant urban pollution" in prompt
        assert "[IMPACT:" in prompt
        assert "1 of 3" in prompt

    def test_turn_prompt_second_claim(self, handler, entity, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        prompt = handler.get_turn_prompt(entity, stress_discussion)
        assert "Public transit" in prompt
        assert "2 of 3" in prompt

    def test_process_response_extracts_score(self, handler, entity, stress_discussion):
        content = "If this claim is false, the conclusion weakens significantly. [IMPACT: 4]"
        result = handler.process_response(content, entity, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert scores["Analyst_1"] == 4

    def test_process_response_no_score(self, handler, entity, stress_discussion):
        content = "The conclusion still mostly holds without this."
        result = handler.process_response(content, entity, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert "Analyst_1" not in scores

    def test_process_response_skips_moderator(self, handler, stress_discussion):
        mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
        content = "Summary of the discussion. [IMPACT: 3]"
        handler.process_response(content, mod, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert "Moderator" not in scores

    def test_should_advance_not_done(self, handler, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        assert handler.should_advance(stress_discussion) is False

    def test_should_advance_all_done(self, handler, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 3
        assert handler.should_advance(stress_discussion) is True

    def test_transition_message(self, handler, stress_discussion):
        msg = handler.get_transition_message(stress_discussion)
        assert "Counterfactual" in msg or "stress" in msg.lower()
        assert "Cars cause significant urban pollution" in msg


from consensus.methods.phases.counterfactual_synthesize import SynthesizeHandler
from consensus.methods.counterfactual import CounterfactualStressTest
from consensus.methods.phases._counterfactual_helpers import (
    classify_claim,
    format_results_table,
)
import consensus.methods as _methods_module
from consensus.methods import get_method, list_methods


class TestSynthesizeHandler:
    @pytest.fixture
    def handler(self):
        return SynthesizeHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "synthesize"
        assert handler.phase.rounds == 1
        assert handler.phase.allow_tools is False

    def test_turn_order_moderator_only(self, handler, cf_discussion):
        entity_ids = [1, 2, 3]
        result = handler.get_turn_order(entity_ids, cf_discussion)
        assert result == [cf_discussion.moderator_id]

    def test_system_prompt_empty(self, handler, entity, cf_discussion):
        assert handler.get_system_prompt(entity, cf_discussion) == ""

    def test_turn_prompt_empty(self, handler, entity, cf_discussion):
        assert handler.get_turn_prompt(entity, cf_discussion) == ""

    def test_transition_message(self, handler, cf_discussion):
        msg = handler.get_transition_message(cf_discussion)
        assert "Synthesis" in msg


class TestCounterfactualStressTestIntegration:
    @pytest.fixture
    def method(self):
        return CounterfactualStressTest()

    @pytest.fixture
    def discussion(self, method):
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        return disc

    # -- Phase auto-derivation --

    def test_phases_auto_derived(self, method):
        assert len(method.default_phases) == 4
        assert method.default_phases[0].name == "cf_deliberate"
        assert method.default_phases[1].name == "extract"
        assert method.default_phases[2].name == "stress_test"
        assert method.default_phases[3].name == "synthesize"

    # -- init_state --

    def test_init_state_default(self, method, discussion):
        state = discussion.method_state
        assert state["current_phase"] == "cf_deliberate"
        assert state["phase_round"] == 1
        assert state["preliminary_conclusion"] is None
        assert state["prior_conclusion"] is None
        assert state["claims"] == []
        assert state["claim_results"] == []
        assert state["current_claim_index"] == 0
        assert state["extraction_failed"] is False
        assert state["extraction_attempts"] == 0

    def test_init_state_with_prior_conclusion(self, method):
        """Test that prior_conclusion skips deliberation and populates state."""
        disc, _ = _make_discussion()
        disc.method_state = {"prior_conclusion": "AI will dominate."}
        state = method.init_state(disc)
        assert state["current_phase"] == "extract"
        assert state["preliminary_conclusion"] == "AI will dominate."
        assert state["prior_conclusion"] == "AI will dominate."

    # -- on_round_complete --

    def test_on_round_complete_non_stress(self, method, discussion):
        discussion.method_state["phase_round"] = 1
        method.on_round_complete(discussion)
        assert discussion.method_state["phase_round"] == 2
        assert discussion.method_state["current_claim_index"] == 0

    def test_on_round_complete_stress_test(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["phase_round"] = 1
        discussion.method_state["claims"] = [
            {"id": 1, "text": "Claim A"},
            {"id": 2, "text": "Claim B"},
        ]
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Claim A",
             "scores": {"Analyst_1": 4, "Analyst_2": 5},
             "avg_score": None, "classification": None},
            {"claim_id": 2, "claim_text": "Claim B",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        discussion.method_state["current_claim_index"] = 0

        method.on_round_complete(discussion)

        assert discussion.method_state["phase_round"] == 2
        assert discussion.method_state["current_claim_index"] == 1
        assert discussion.method_state["claim_results"][0]["avg_score"] == 4.5
        assert discussion.method_state["claim_results"][0]["classification"] == "LOAD-BEARING"

    def test_on_round_complete_stress_empty_scores(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "C",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        discussion.method_state["current_claim_index"] = 0

        method.on_round_complete(discussion)

        assert discussion.method_state["current_claim_index"] == 1
        assert discussion.method_state["claim_results"][0]["avg_score"] is None

    # -- get_conclusion_prompt --

    def test_get_conclusion_prompt(self, method, discussion):
        discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Pollution claim",
             "scores": {"A": 5}, "avg_score": 5.0, "classification": "LOAD-BEARING"},
            {"claim_id": 2, "claim_text": "Transit claim",
             "scores": {"A": 1}, "avg_score": 1.0, "classification": "DECORATIVE"},
        ]
        prompt = method.get_conclusion_prompt(discussion)
        assert "Cars should be banned" in prompt
        assert "Pollution claim" in prompt
        assert "LOAD-BEARING" in prompt
        assert "DECORATIVE" in prompt
        assert "robustness" in prompt.lower() or "robust" in prompt.lower()

    def test_get_conclusion_prompt_no_claims(self, method, discussion):
        discussion.method_state["claims"] = []
        prompt = method.get_conclusion_prompt(discussion)
        assert "no claims" in prompt.lower() or "could not" in prompt.lower()

    # -- Method delegation --

    def test_system_prompt_deliberate(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        prompt = method.get_system_prompt(entity, discussion)
        assert "preliminary" in prompt.lower()
        assert discussion.topic in prompt

    def test_system_prompt_stress(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "Test claim"}]
        discussion.method_state["current_claim_index"] = 0
        prompt = method.get_system_prompt(entity, discussion)
        assert "Test claim" in prompt
        assert "FALSE" in prompt

    # -- Phase advancement --

    def test_advance_deliberate_to_extract(self, method, discussion):
        discussion.method_state["phase_round"] = 3
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "extract"

    def test_advance_extract_to_stress(self, method, discussion):
        discussion.method_state["current_phase"] = "extract"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "stress_test"

    def test_advance_stress_to_synthesize(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        discussion.method_state["current_claim_index"] = 1
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "synthesize"

    def test_advance_chain_with_no_claims(self, method, discussion):
        """3 failed extractions -> stress_test immediately advances -> synthesize."""
        discussion.method_state["current_phase"] = "extract"
        discussion.method_state["claims"] = []
        discussion.method_state["extraction_attempts"] = 3
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "stress_test"
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "synthesize"

    def test_advance_synthesize_to_none(self, method, discussion):
        discussion.method_state["current_phase"] = "synthesize"
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new is None


class TestMethodRegistration:
    def setup_method(self):
        """Clear cached singletons to avoid stale test state."""
        _methods_module._METHODS_METADATA = None
        _methods_module._INSTANCES.pop("counterfactual", None)

    def test_get_method(self):
        method = get_method("counterfactual")
        assert isinstance(method, CounterfactualStressTest)
        assert method.name == "counterfactual"

    def test_list_methods_includes_counterfactual(self):
        methods = list_methods()
        names = [m["name"] for m in methods]
        assert "counterfactual" in names

    def test_method_to_dict(self):
        method = CounterfactualStressTest()
        d = method.to_dict()
        assert d["name"] == "counterfactual"
        assert len(d["phases"]) == 4
        assert d["phases"][0]["name"] == "cf_deliberate"
