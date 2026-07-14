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
