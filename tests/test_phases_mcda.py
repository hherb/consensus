"""Tests for Weighted Decision Matrix phase handlers (issue #25).

Handler-level coverage: prompts, free-text fallback parsing,
advancement/give-up caps, the options/criteria aborts, the decision
artifact fallback, and method-level assembly.
"""

import pytest

from consensus.methods.base import LINEAR_NEXT
from consensus.methods.phases._mcda_helpers import (
    MAX_CRITERIA_ROUNDS,
    MAX_OPTIONS_ROUNDS,
    record_criteria,
    record_options,
    record_scores,
)
from consensus.methods.phases.enumerate_options import EnumerateOptionsHandler
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity() -> Entity:
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator() -> Entity:
    return Entity(name="Mod", entity_type=EntityType.AI, id=99)


def make_disc(**state) -> Discussion:
    """A decision-matrix discussion with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="Should we build, buy, or rent our data platform?",
                      discussion_method="decision_matrix",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = {"current_phase": "options", "phase_round": 1,
                         "options": [], "criteria": []}
    disc.method_state.update(state)
    return disc


def populated_disc(**state) -> Discussion:
    """A discussion with two options, two criteria, and one scorer."""
    disc = make_disc(**state)
    alice = disc.entities[1]
    record_options(disc.method_state, alice,
                   ["Build the platform in-house",
                    "Buy a commercial platform"])
    record_criteria(disc.method_state, alice,
                    [{"name": "Total cost", "weight": 4},
                     {"name": "Time to market", "weight": 2}])
    record_scores(disc.method_state, alice,
                  {"O1": {"C1": 4, "C2": 2}, "O2": {"C1": 2, "C2": 5}})
    return disc


OPTION_LINES = (
    "1. Build the platform in-house with the existing team\n"
    "2. Buy a commercial data platform"
)


class TestEnumerateOptionsHandler:
    def test_phase_metadata(self):
        handler = EnumerateOptionsHandler()
        assert handler.phase.name == "options"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        assert EnumerateOptionsHandler().init_state(make_disc()) == {
            "options": []}

    def test_system_prompt(self, ai_entity):
        prompt = EnumerateOptionsHandler().get_system_prompt(
            ai_entity, make_disc())
        assert "OPTION ENUMERATION" in prompt
        assert "TestAI" in prompt
        assert "data platform" in prompt
        assert "submit_options" in prompt

    def test_turn_prompt_names_tool(self, ai_entity):
        assert "submit_options" in EnumerateOptionsHandler().get_turn_prompt(
            ai_entity, make_disc())

    def test_process_response_records_numbered_list(self, ai_entity):
        disc = make_disc()
        EnumerateOptionsHandler().process_response(
            OPTION_LINES, ai_entity, disc)
        assert len(disc.method_state["options"]) == 2
        assert disc.method_state["options"][0]["entity_id"] == 1

    def test_process_response_prose_records_nothing(self, ai_entity):
        disc = make_disc()
        EnumerateOptionsHandler().process_response(
            "I think we have several ways forward.", ai_entity, disc)
        assert disc.method_state["options"] == []

    def test_advances_when_options_recorded(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_options(disc.method_state, ai_entity,
                       ["Build the platform in-house"])
        assert EnumerateOptionsHandler().should_advance(disc) is True

    def test_does_not_advance_mid_round(self, ai_entity):
        disc = make_disc(phase_round=1)
        record_options(disc.method_state, ai_entity,
                       ["Build the platform in-house"])
        assert EnumerateOptionsHandler().should_advance(disc) is False

    def test_gives_up_after_max_rounds(self):
        disc = make_disc(phase_round=MAX_OPTIONS_ROUNDS + 1)
        assert EnumerateOptionsHandler().should_advance(disc) is True

    def test_next_phase_linear_with_options(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_options(disc.method_state, ai_entity,
                       ["Build the platform in-house"])
        assert EnumerateOptionsHandler().next_phase(disc) == LINEAR_NEXT

    def test_aborts_when_no_options_after_give_up(self):
        disc = make_disc(phase_round=MAX_OPTIONS_ROUNDS + 1)
        handler = EnumerateOptionsHandler()
        assert handler.next_phase(disc) is None
        assert "ended early" in handler.get_method_complete_message(disc)

    def test_no_abort_message_on_normal_flow(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_options(disc.method_state, ai_entity,
                       ["Build the platform in-house"])
        assert EnumerateOptionsHandler().get_method_complete_message(
            disc) == ""


from consensus.methods.phases.weight_criteria import (  # noqa: E402
    WeightCriteriaHandler,
)


class TestWeightCriteriaHandler:
    def disc_with_options(self, **state) -> Discussion:
        disc = make_disc(current_phase="criteria", **state)
        record_options(disc.method_state, disc.entities[1],
                       ["Build the platform in-house",
                        "Buy a commercial platform"])
        return disc

    def test_phase_metadata(self):
        handler = WeightCriteriaHandler()
        assert handler.phase.name == "criteria"
        assert handler.phase.rounds == 2

    def test_init_state(self):
        assert WeightCriteriaHandler().init_state(make_disc()) == {
            "criteria": []}

    def test_system_prompt(self, ai_entity):
        prompt = WeightCriteriaHandler().get_system_prompt(
            ai_entity, self.disc_with_options())
        assert "CRITERIA & WEIGHTS" in prompt
        assert "submit_weighted_criteria" in prompt
        assert "O1: Build the platform in-house" in prompt

    def test_refinement_turn_prompt_differs(self, ai_entity):
        handler = WeightCriteriaHandler()
        first = handler.get_turn_prompt(
            ai_entity, self.disc_with_options(phase_round=1))
        second = handler.get_turn_prompt(
            ai_entity, self.disc_with_options(phase_round=2))
        assert first != second
        assert "submit_weighted_criteria" in first
        assert "submit_weighted_criteria" in second

    def test_process_response_parses_weighted_list(self, ai_entity):
        disc = self.disc_with_options()
        WeightCriteriaHandler().process_response(
            "1. Total cost of ownership (weight: 4)\n"
            "2. Time to market (weight: 2)",
            ai_entity, disc)
        criteria = disc.method_state["criteria"]
        assert [c["name"] for c in criteria] == [
            "Total cost of ownership", "Time to market"]
        assert criteria[0]["weight_votes"] == {"1": 4}

    def test_advances_after_both_rounds_with_criteria(self, ai_entity):
        disc = self.disc_with_options(phase_round=3)
        record_criteria(disc.method_state, ai_entity,
                        [{"name": "Total cost", "weight": 4}])
        assert WeightCriteriaHandler().should_advance(disc) is True

    def test_does_not_advance_during_refinement_round(self, ai_entity):
        disc = self.disc_with_options(phase_round=2)
        record_criteria(disc.method_state, ai_entity,
                        [{"name": "Total cost", "weight": 4}])
        assert WeightCriteriaHandler().should_advance(disc) is False

    def test_gives_up_after_max_rounds(self):
        disc = self.disc_with_options(phase_round=MAX_CRITERIA_ROUNDS + 1)
        assert WeightCriteriaHandler().should_advance(disc) is True

    def test_aborts_when_no_criteria_after_give_up(self):
        disc = self.disc_with_options(phase_round=MAX_CRITERIA_ROUNDS + 1)
        handler = WeightCriteriaHandler()
        assert handler.next_phase(disc) is None
        assert "ended early" in handler.get_method_complete_message(disc)

    def test_linear_next_with_criteria(self, ai_entity):
        disc = self.disc_with_options(phase_round=3)
        record_criteria(disc.method_state, ai_entity,
                        [{"name": "Total cost", "weight": 4}])
        handler = WeightCriteriaHandler()
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert handler.get_method_complete_message(disc) == ""


from consensus.methods.phases.score_options import (  # noqa: E402
    ScoreOptionsHandler,
)


class TestScoreOptionsHandler:
    def test_phase_metadata(self):
        handler = ScoreOptionsHandler()
        assert handler.phase.name == "score"
        assert handler.phase.rounds == 1

    def test_system_prompt_embeds_options_and_criteria(self, ai_entity):
        disc = populated_disc(current_phase="score")
        prompt = ScoreOptionsHandler().get_system_prompt(ai_entity, disc)
        assert "SCORING PHASE" in prompt
        assert "O1: Build the platform in-house" in prompt
        assert "C1: Total cost" in prompt
        assert "submit_scores" in prompt

    def test_degenerate_prompt_omits_tool(self, ai_entity):
        disc = make_disc(current_phase="score")
        prompt = ScoreOptionsHandler().get_system_prompt(ai_entity, disc)
        assert "submit_scores" not in prompt
        assert ScoreOptionsHandler().get_output_tool(ai_entity, disc) is None

    def test_process_response_records_json_scores(self, ai_entity):
        disc = populated_disc(current_phase="score")
        disc.method_state.pop("scores")
        ScoreOptionsHandler().process_response(
            'My scores:\n```json\n'
            '{"scores": {"O1": {"C1": 5, "C2": 1}}}\n```',
            ai_entity, disc)
        assert disc.method_state["scores"]["1"] == {"O1": {"C1": 5,
                                                           "C2": 1}}

    def test_process_response_prose_records_nothing(self, ai_entity):
        disc = populated_disc(current_phase="score")
        disc.method_state.pop("scores")
        ScoreOptionsHandler().process_response(
            "I broadly prefer the first option.", ai_entity, disc)
        assert "scores" not in disc.method_state

    def test_advances_after_single_round(self):
        assert ScoreOptionsHandler().should_advance(
            populated_disc(current_phase="score", phase_round=2)) is True
        assert ScoreOptionsHandler().should_advance(
            populated_disc(current_phase="score", phase_round=1)) is False


from consensus.methods.phases.analyse_sensitivity import (  # noqa: E402
    SensitivityHandler,
)


class TestSensitivityHandler:
    def test_phase_metadata(self):
        handler = SensitivityHandler()
        assert handler.phase.name == "sensitivity"
        assert handler.phase.rounds == 1
        assert handler.requires_structured_output is False

    def test_moderator_only_turn_order(self):
        disc = populated_disc(current_phase="sensitivity")
        assert SensitivityHandler().get_turn_order([1, 2], disc) == [99]

    def test_system_prompt_embeds_computed_analysis(self, moderator):
        disc = populated_disc(current_phase="sensitivity")
        prompt = SensitivityHandler().get_system_prompt(moderator, disc)
        assert "SENSITIVITY ANALYSIS" in prompt
        # Weighted ranking computed from the recorded scores:
        # O1 = 4*4 + 2*2 = 20, O2 = 4*2 + 2*5 = 18.
        assert "weighted total 20.0" in prompt
        assert "Pivotal" in prompt

    def test_no_structured_tool(self, moderator):
        disc = populated_disc(current_phase="sensitivity")
        assert SensitivityHandler().get_output_tool(moderator, disc) is None

    def test_advances_after_single_round(self):
        assert SensitivityHandler().should_advance(
            populated_disc(current_phase="sensitivity",
                           phase_round=2)) is True

    def test_transition_message_shows_ranking(self):
        disc = populated_disc(current_phase="sensitivity")
        assert "weighted total" in \
            SensitivityHandler().get_transition_message(disc)
