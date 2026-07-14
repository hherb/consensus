"""Structured-output coverage for the MCDA phases (#23 convention).

The forced submit_options / submit_weighted_criteria / submit_scores /
submit_decision tools replace free-text parsing for tool-capable
models; the free-text paths remain for humans.  Mirrors
test_ngt_structured.
"""

from consensus.methods import get_method
from consensus.methods.phases._mcda_helpers import (
    CRITERIA_TOOL_PARAMETERS,
    DECISION_TOOL_PARAMETERS,
    OPTIONS_TOOL_PARAMETERS,
    SCORES_TOOL_PARAMETERS,
    record_criteria,
    record_options,
)
from consensus.methods.phases.analyse_sensitivity import SensitivityHandler
from consensus.methods.phases.decide import DecideHandler
from consensus.methods.phases.enumerate_options import EnumerateOptionsHandler
from consensus.methods.phases.score_options import ScoreOptionsHandler
from consensus.methods.phases.weight_criteria import WeightCriteriaHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="Should we build, buy, or rent our data platform?",
                      discussion_method="decision_matrix",
                      moderator_id=99)
    disc.method_state = get_method("decision_matrix").init_state(disc)
    disc.method_state["current_phase"] = phase
    disc.method_state.update(state)
    return disc


def _populate(disc: Discussion) -> Discussion:
    """Record two options and two criteria into a discussion's state."""
    record_options(disc.method_state, _entity(),
                   ["Build the platform in-house",
                    "Buy a commercial platform"])
    record_criteria(disc.method_state, _entity(),
                    [{"name": "Total cost", "weight": 4},
                     {"name": "Time to market", "weight": 2}])
    return disc


class TestStructuredFlags:
    def test_options_criteria_score_decide_require_structured(self):
        assert EnumerateOptionsHandler().requires_structured_output is True
        assert WeightCriteriaHandler().requires_structured_output is True
        assert ScoreOptionsHandler().requires_structured_output is True
        assert DecideHandler().requires_structured_output is True

    def test_sensitivity_does_not(self):
        assert SensitivityHandler().requires_structured_output is False

    def test_method_requires_structured_output(self):
        assert (get_method("decision_matrix").requires_structured_output()
                is True)


class TestOutputToolSpecs:
    def test_options_spec(self):
        spec = EnumerateOptionsHandler().get_output_tool(
            _entity(), _discussion("options"))
        assert spec.name == "submit_options"
        assert spec.parameters is OPTIONS_TOOL_PARAMETERS

    def test_criteria_spec(self):
        spec = WeightCriteriaHandler().get_output_tool(
            _entity(), _discussion("criteria"))
        assert spec.name == "submit_weighted_criteria"
        assert spec.parameters is CRITERIA_TOOL_PARAMETERS

    def test_scores_spec_embeds_labels(self):
        disc = _populate(_discussion("score"))
        spec = ScoreOptionsHandler().get_output_tool(_entity(), disc)
        assert spec.name == "submit_scores"
        assert spec.parameters is SCORES_TOOL_PARAMETERS
        assert "O1" in spec.description
        assert "C1" in spec.description

    def test_scores_spec_none_when_degenerate(self):
        assert ScoreOptionsHandler().get_output_tool(
            _entity(), _discussion("score")) is None

    def test_decision_spec(self):
        disc = _populate(_discussion("decide"))
        spec = DecideHandler().get_output_tool(_entity(99, "Mod"), disc)
        assert spec.name == "submit_decision"
        assert spec.parameters is DECISION_TOOL_PARAMETERS

    def test_decision_spec_none_without_options(self):
        assert DecideHandler().get_output_tool(
            _entity(99, "Mod"), _discussion("decide")) is None


class TestPromptsNameTheTool:
    def test_options_prompts(self):
        handler = EnumerateOptionsHandler()
        disc = _discussion("options")
        assert "submit_options" in handler.get_system_prompt(_entity(), disc)
        assert "submit_options" in handler.get_turn_prompt(_entity(), disc)

    def test_criteria_prompts(self):
        handler = WeightCriteriaHandler()
        disc = _discussion("criteria")
        assert "submit_weighted_criteria" in handler.get_system_prompt(
            _entity(), disc)
        assert "submit_weighted_criteria" in handler.get_turn_prompt(
            _entity(), disc)

    def test_scores_prompts(self):
        handler = ScoreOptionsHandler()
        disc = _populate(_discussion("score"))
        assert "submit_scores" in handler.get_system_prompt(_entity(), disc)
        assert "submit_scores" in handler.get_turn_prompt(_entity(), disc)

    def test_decision_prompts(self):
        handler = DecideHandler()
        disc = _populate(_discussion("decide"))
        assert "submit_decision" in handler.get_system_prompt(
            _entity(99, "Mod"), disc)
        assert "submit_decision" in handler.get_turn_prompt(
            _entity(99, "Mod"), disc)

    def test_degenerate_score_prompts_omit_tool(self):
        handler = ScoreOptionsHandler()
        disc = _discussion("score")
        assert "submit_scores" not in handler.get_system_prompt(
            _entity(), disc)
        assert "submit_scores" not in handler.get_turn_prompt(
            _entity(), disc)


class TestStructuredMatchesFreeTextPaths:
    def test_options_structured_and_free_text_produce_same_state(self):
        texts = ["Build the platform in-house with the existing team",
                 "Buy a commercial data platform"]
        handler = EnumerateOptionsHandler()

        disc_a = _discussion("options")
        handler.process_structured_response(
            {"options": texts, "reasoning": "The realistic choices."},
            _entity(), disc_a)

        disc_b = _discussion("options")
        handler.process_response(
            "1. " + texts[0] + "\n2. " + texts[1], _entity(), disc_b)

        assert ([o["text"] for o in disc_a.method_state["options"]]
                == [o["text"] for o in disc_b.method_state["options"]])

    def test_criteria_structured_and_free_text_produce_same_state(self):
        handler = WeightCriteriaHandler()

        disc_a = _discussion("criteria")
        handler.process_structured_response(
            {"criteria": [{"name": "Total cost of ownership", "weight": 4},
                          {"name": "Time to market", "weight": 2}],
             "reasoning": "Cost dominates."},
            _entity(), disc_a)

        disc_b = _discussion("criteria")
        handler.process_response(
            "1. Total cost of ownership (weight: 4)\n"
            "2. Time to market (weight: 2)",
            _entity(), disc_b)

        def key(state: dict) -> list[tuple]:
            return [(c["name"], c["weight_votes"])
                    for c in state["criteria"]]

        assert key(disc_a.method_state) == key(disc_b.method_state)

    def test_scores_structured_and_free_text_produce_same_state(self):
        handler = ScoreOptionsHandler()
        scores = {"O1": {"C1": 4, "C2": 2}, "O2": {"C1": 2, "C2": 5}}

        disc_a = _populate(_discussion("score"))
        handler.process_structured_response(
            {"scores": scores, "reasoning": "Cost favours in-house."},
            _entity(), disc_a)

        disc_b = _populate(_discussion("score"))
        handler.process_response(
            'My scores:\n```json\n'
            '{"scores": {"O1": {"C1": 4, "C2": 2}, '
            '"O2": {"C1": 2, "C2": 5}}}\n```',
            _entity(), disc_b)

        assert (disc_a.method_state["scores"]
                == disc_b.method_state["scores"])

    def test_decision_both_paths_record_an_artifact(self):
        handler = DecideHandler()

        disc_a = _populate(_discussion("decide"))
        handler.process_structured_response(
            {"recommended_option_id": 1,
             "rationale": "Wins on the dominant cost criterion."},
            _entity(99, "Mod"), disc_a)

        disc_b = _populate(_discussion("decide"))
        handler.process_response(
            "On balance the in-house option wins.", _entity(99, "Mod"),
            disc_b)

        art_a = disc_a.method_state["decision_artifact"]
        art_b = disc_b.method_state["decision_artifact"]
        assert art_a["recommended_option_id"] == 1
        # Free-text fallback defaults to the top-ranked option and
        # flags the default in a caveat.
        assert art_b["recommended_option_id"] == art_a["ranking"][0][
            "option_id"]
        assert any("top-ranked" in c for c in art_b["caveats"])
        assert art_a["ranking"] == art_b["ranking"]
