"""Tests for the Tree of Thoughts phase handlers (issue #26).

Handler-level coverage: prompts, free-text fallback parsing,
anonymisation, advancement/give-up caps, the propose-phase abort,
deterministic prune routing (loop / converged / depth budget /
degenerate), turn orders, and method-level assembly.
"""

import pytest

from consensus.methods.base import LINEAR_NEXT
from consensus.methods.phases._tot_helpers import (
    MAX_PROPOSE_ROUNDS,
    MAX_TOT_DEPTH,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    STOP_DEPTH,
    compute_beam,
    record_thought_scores,
    record_thoughts,
)
from consensus.methods.phases.propose_thoughts import ProposeThoughtsHandler
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity() -> Entity:
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator() -> Entity:
    return Entity(name="Mod", entity_type=EntityType.AI, id=99)


def _base_state() -> dict:
    """The union of all ToT handlers' init_state keys, built by hand so
    handler tests do not depend on the (later-task) method registration."""
    return {"current_phase": "propose", "phase_round": 1, "thoughts": [],
            "thought_scores": {}, "beam_history": [], "tot_artifact": {},
            "expansions": []}


def make_disc(**state) -> Discussion:
    """A discussion in the ToT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How should we grow the developer community?",
                      discussion_method="tree_of_thoughts",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = _base_state()
    disc.method_state.update(state)
    return disc


THOUGHT_LINES = (
    "1. Launch a public plugin marketplace with revenue sharing\n"
    "2. Run quarterly community hackathons with sponsored prizes"
)

DISTINCT_TEXTS = [
    "Launch a public plugin marketplace with revenue sharing",
    "Run quarterly community hackathons with sponsored prizes",
    "Publish a certification program for advanced practitioners",
    "Fund an ambassador program in regional user groups",
]


def _seed_thoughts(disc: Discussion, n: int = 4) -> None:
    record_thoughts(disc.method_state,
                    Entity(name="Seed", entity_type=EntityType.AI, id=50),
                    DISTINCT_TEXTS[:n])
    assert len(disc.method_state["thoughts"]) == n


class TestProposeThoughtsHandler:
    def test_phase_metadata(self):
        handler = ProposeThoughtsHandler()
        assert handler.phase.name == "propose"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        assert ProposeThoughtsHandler().init_state(make_disc()) == {
            "thoughts": []}

    def test_system_prompt_names_tool_and_topic(self, ai_entity):
        prompt = ProposeThoughtsHandler().get_system_prompt(
            ai_entity, make_disc())
        assert "submit_thoughts" in prompt
        assert "TestAI" in prompt
        assert "developer community" in prompt
        assert "distinct" in prompt.lower()

    def test_context_is_anonymised(self, ai_entity):
        disc = make_disc()
        filtered = ProposeThoughtsHandler().filter_context_message(
            "TestAI", "TestAI: my proposal", "assistant", disc,
            current_entity_id=2)
        assert "TestAI" not in filtered

    def test_free_text_fallback_parses_numbered_list(self, ai_entity):
        disc = make_disc()
        result = ProposeThoughtsHandler().process_response(
            THOUGHT_LINES, ai_entity, disc)
        assert len(disc.method_state["thoughts"]) == 2
        assert result.display_content == THOUGHT_LINES

    def test_unparseable_free_text_records_nothing(self, ai_entity):
        disc = make_disc()
        ProposeThoughtsHandler().process_response(
            "I have no list for you.", ai_entity, disc)
        assert disc.method_state["thoughts"] == []

    def test_advances_after_round_with_thoughts(self):
        disc = make_disc(phase_round=2)
        _seed_thoughts(disc, 2)
        assert ProposeThoughtsHandler().should_advance(disc) is True

    def test_waits_when_no_thoughts_within_budget(self):
        disc = make_disc(phase_round=2)
        assert ProposeThoughtsHandler().should_advance(disc) is False

    def test_gives_up_after_max_rounds(self):
        disc = make_disc(phase_round=MAX_PROPOSE_ROUNDS + 1)
        assert ProposeThoughtsHandler().should_advance(disc) is True

    def test_next_phase_aborts_on_giveup_without_thoughts(self):
        disc = make_disc(phase_round=MAX_PROPOSE_ROUNDS + 1)
        assert ProposeThoughtsHandler().next_phase(disc) is None
        message = ProposeThoughtsHandler().get_method_complete_message(disc)
        assert "ended early" in message.lower()

    def test_next_phase_linear_with_thoughts(self):
        disc = make_disc(phase_round=2)
        _seed_thoughts(disc, 2)
        assert ProposeThoughtsHandler().next_phase(disc) == LINEAR_NEXT
        assert ProposeThoughtsHandler().get_method_complete_message(
            disc) == ""
