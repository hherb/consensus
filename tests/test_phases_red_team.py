"""Tests for Red Team / Blue Team phase handlers.

Tests each handler in isolation AND verifies the refactored method
produces identical behavior to the original monolithic implementation.
"""

import pytest

from consensus.methods import get_method
from consensus.methods.phases.construct import ConstructHandler
from consensus.methods.phases.attack import AttackHandler
from consensus.methods.phases.revise_red_team import ReviseRedTeamHandler
from consensus.methods.phases.assess_red_team import AssessRedTeamHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

@pytest.fixture
def red_entity():
    return Entity(name="RedAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def blue_entity():
    return Entity(name="BlueAI", entity_type=EntityType.AI, id=2)


@pytest.fixture
def blue_entity2():
    return Entity(name="BlueAI2", entity_type=EntityType.AI, id=3)


@pytest.fixture
def construct_handler():
    return ConstructHandler()


@pytest.fixture
def attack_handler():
    return AttackHandler()


@pytest.fixture
def revise_handler():
    return ReviseRedTeamHandler()


@pytest.fixture
def assess_handler():
    return AssessRedTeamHandler()


@pytest.fixture
def disc(red_entity, blue_entity, blue_entity2):
    method = get_method("red_team")
    d = Discussion(topic="Should we adopt microservices?",
                   discussion_method="red_team")
    d.method_state = method.init_state(d)
    d.entities = [red_entity, blue_entity, blue_entity2]
    return d


# -- ConstructHandler tests --

class TestConstructHandler:
    def test_init_state(self, construct_handler, disc):
        state = construct_handler.init_state(disc)
        assert state["red_team_entity_id"] is None
        assert state["red_team_rotation"] == 0
        assert state["attacks"] == []

    def test_turn_order_assigns_red_team_if_none(self, construct_handler, disc):
        assert disc.method_state["red_team_entity_id"] is None
        order = construct_handler.get_turn_order([1, 2, 3], disc)
        assert disc.method_state["red_team_entity_id"] == 1
        # Red team excluded from construction
        assert 1 not in order
        assert order == [2, 3]

    def test_turn_order_excludes_existing_red_team(self, construct_handler, disc):
        disc.method_state["red_team_entity_id"] = 2
        order = construct_handler.get_turn_order([1, 2, 3], disc)
        assert order == [1, 3]

    def test_system_prompt_red_team_silent(self, construct_handler,
                                            red_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = construct_handler.get_system_prompt(red_entity, disc)
        assert "SILENT" in prompt
        assert "RED TEAM" in prompt

    def test_system_prompt_blue_team_construct(self, construct_handler,
                                                blue_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = construct_handler.get_system_prompt(blue_entity, disc)
        assert "CONSTRUCTION PHASE" in prompt
        assert "BLUE TEAM" in prompt

    def test_system_prompt_contains_entity_name(self, construct_handler,
                                                  red_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = construct_handler.get_system_prompt(red_entity, disc)
        assert "RedAI" in prompt

    def test_system_prompt_contains_topic(self, construct_handler,
                                           blue_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = construct_handler.get_system_prompt(blue_entity, disc)
        assert "Should we adopt microservices?" in prompt

    def test_turn_prompt(self, construct_handler, blue_entity, disc):
        prompt = construct_handler.get_turn_prompt(blue_entity, disc)
        assert "BlueAI" in prompt
        assert "Blue Team member" in prompt

    def test_summary_prompt(self, construct_handler, disc):
        prompt = construct_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt

    def test_should_advance_default(self, construct_handler, disc):
        # round 1, phase has rounds=1 -> not yet (phase_round must exceed rounds)
        disc.method_state["phase_round"] = 1
        assert construct_handler.should_advance(disc) is False

        disc.method_state["phase_round"] = 2
        assert construct_handler.should_advance(disc) is True


# -- AttackHandler tests --

class TestAttackHandler:
    def test_turn_order_red_first(self, attack_handler, disc):
        disc.method_state["red_team_entity_id"] = 1
        order = attack_handler.get_turn_order([1, 2, 3], disc)
        assert order[0] == 1
        assert order == [1, 2, 3]

    def test_turn_order_red_first_when_not_first_in_input(self, attack_handler,
                                                            disc):
        disc.method_state["red_team_entity_id"] = 2
        order = attack_handler.get_turn_order([1, 2, 3], disc)
        assert order[0] == 2
        assert order == [2, 1, 3]

    def test_turn_order_no_red_id(self, attack_handler, disc):
        disc.method_state["red_team_entity_id"] = None
        order = attack_handler.get_turn_order([1, 2, 3], disc)
        assert order == [1, 2, 3]

    def test_system_prompt_red_team_attack(self, attack_handler,
                                            red_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = attack_handler.get_system_prompt(red_entity, disc)
        assert "RED TEAM ATTACK PHASE" in prompt
        assert "DESTRUCTION" in prompt

    def test_system_prompt_blue_team_defense(self, attack_handler,
                                              blue_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = attack_handler.get_system_prompt(blue_entity, disc)
        assert "DEFENSE PHASE" in prompt
        assert "BLUE TEAM" in prompt

    def test_turn_prompt_red(self, attack_handler, red_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = attack_handler.get_turn_prompt(red_entity, disc)
        assert "Red Team" in prompt
        assert "attack" in prompt

    def test_turn_prompt_blue(self, attack_handler, blue_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = attack_handler.get_turn_prompt(blue_entity, disc)
        assert "Blue Team" in prompt
        assert "defend" in prompt

    def test_transition_message_includes_red_name(self, attack_handler, disc):
        disc.method_state["red_team_entity_id"] = 1
        msg = attack_handler.get_transition_message(disc)
        assert "RedAI" in msg
        assert "Red Team" in msg

    def test_transition_message_fallback_name(self, attack_handler, disc):
        disc.method_state["red_team_entity_id"] = 999
        disc.entities = []
        msg = attack_handler.get_transition_message(disc)
        assert "Entity 999" in msg

    def test_summary_prompt(self, attack_handler, disc):
        prompt = attack_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt

    def test_should_advance_rounds_2(self, attack_handler, disc):
        disc.method_state["phase_round"] = 2
        assert attack_handler.should_advance(disc) is False

        disc.method_state["phase_round"] = 3
        assert attack_handler.should_advance(disc) is True


# -- ReviseRedTeamHandler tests --

class TestReviseRedTeamHandler:
    def test_turn_order_excludes_red(self, revise_handler, disc):
        disc.method_state["red_team_entity_id"] = 1
        order = revise_handler.get_turn_order([1, 2, 3], disc)
        assert 1 not in order
        assert order == [2, 3]

    def test_system_prompt_red_silent(self, revise_handler, red_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = revise_handler.get_system_prompt(red_entity, disc)
        assert "SILENT" in prompt
        assert "RED TEAM" in prompt

    def test_system_prompt_blue_revise(self, revise_handler, blue_entity, disc):
        disc.method_state["red_team_entity_id"] = 1
        prompt = revise_handler.get_system_prompt(blue_entity, disc)
        assert "REVISION PHASE" in prompt
        assert "BLUE TEAM" in prompt

    def test_turn_prompt(self, revise_handler, blue_entity, disc):
        prompt = revise_handler.get_turn_prompt(blue_entity, disc)
        assert "BlueAI" in prompt
        assert "revise" in prompt

    def test_transition_message(self, revise_handler, disc):
        msg = revise_handler.get_transition_message(disc)
        assert "Revision" in msg
        assert "attack/defense" in msg

    def test_summary_prompt(self, revise_handler, disc):
        prompt = revise_handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


# -- AssessRedTeamHandler tests --

class TestAssessRedTeamHandler:
    def test_system_prompt_empty(self, assess_handler, red_entity, disc):
        prompt = assess_handler.get_system_prompt(red_entity, disc)
        assert prompt == ""

    def test_turn_prompt_empty(self, assess_handler, red_entity, disc):
        prompt = assess_handler.get_turn_prompt(red_entity, disc)
        assert prompt == ""

    def test_transition_message(self, assess_handler, disc):
        msg = assess_handler.get_transition_message(disc)
        assert "Assessment" in msg
        assert "moderator" in msg


# -- Equivalence tests: refactored method matches original behavior --

class TestRedTeamEquivalence:
    def test_init_state_matches(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        state = method.init_state(disc)
        assert state["current_phase"] == "construct"
        assert state["phase_round"] == 1
        assert state["red_team_entity_id"] is None
        assert state["red_team_rotation"] == 0
        assert state["attacks"] == []

    def test_has_four_phases(self):
        method = get_method("red_team")
        assert len(method.default_phases) == 4
        names = [p.name for p in method.default_phases]
        assert names == ["construct", "attack", "revise", "assess"]

    def test_phase_display_names(self):
        method = get_method("red_team")
        display = [p.display_name for p in method.default_phases]
        assert display == [
            "Construction", "Red Team Attack", "Revision", "Assessment"
        ]

    def test_phase_rounds(self):
        method = get_method("red_team")
        rounds = [p.rounds for p in method.default_phases]
        assert rounds == [1, 2, 1, 1]

    def test_construct_system_prompt_via_method(self, blue_entity):
        method = get_method("red_team")
        disc = Discussion(topic="test topic",
                          discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["red_team_entity_id"] = 1
        prompt = method.get_system_prompt(blue_entity, disc)
        assert "CONSTRUCTION PHASE" in prompt
        assert "BlueAI" in prompt
        assert "test topic" in prompt

    def test_attack_system_prompt_via_method(self, red_entity):
        method = get_method("red_team")
        disc = Discussion(topic="test topic",
                          discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "attack"
        disc.method_state["red_team_entity_id"] = 1
        prompt = method.get_system_prompt(red_entity, disc)
        assert "RED TEAM ATTACK PHASE" in prompt

    def test_turn_order_construct_via_method(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        order = method.get_turn_order([1, 2, 3], disc)
        assert disc.method_state["red_team_entity_id"] == 1
        assert order == [2, 3]

    def test_turn_order_attack_via_method(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "attack"
        disc.method_state["red_team_entity_id"] = 2
        order = method.get_turn_order([1, 2, 3], disc)
        assert order == [2, 1, 3]

    def test_turn_order_revise_via_method(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["current_phase"] = "revise"
        disc.method_state["red_team_entity_id"] = 1
        order = method.get_turn_order([1, 2, 3], disc)
        assert order == [2, 3]

    def test_conclusion_prompt(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        prompt = method.get_conclusion_prompt(disc)
        assert "Red Team / Blue Team analysis is complete" in prompt
        assert "Robustness rating" in prompt

    def test_phase_handlers_set(self):
        method = get_method("red_team")
        assert len(method.phase_handlers) == 4

    def test_should_advance_via_method(self):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        # construct has rounds=1, phase_round=1 -> not yet
        assert method.should_advance_phase(disc) is False
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True

    def test_phase_transition_message_attack(self, red_entity):
        method = get_method("red_team")
        disc = Discussion(topic="test", discussion_method="red_team")
        disc.method_state = method.init_state(disc)
        disc.method_state["red_team_entity_id"] = 1
        disc.entities = [red_entity]
        attack_phase = method.default_phases[1]
        msg = method.get_phase_transition_message(attack_phase, disc)
        assert "RedAI" in msg
        assert "Red Team" in msg
