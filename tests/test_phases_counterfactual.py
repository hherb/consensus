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
