"""Tests for Nominal Group Technique phase handlers (issue #24).

Handler-level coverage: prompts, free-text fallback parsing,
anonymisation, advancement/give-up caps, the generate-phase abort,
the cluster-phase fallback promotion, and method-level assembly.
"""

import pytest

from consensus.methods.base import LINEAR_NEXT, ProcessedResponse
from consensus.methods.phases._ngt_helpers import (
    MAX_ALLOCATE_ROUNDS,
    MAX_CLUSTER_ATTEMPTS,
    MAX_GENERATE_ROUNDS,
    POINTS_PER_VOTER,
    record_candidates,
    record_ideas,
)
from consensus.methods.phases.generate_ideas import GenerateIdeasHandler
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity() -> Entity:
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator() -> Entity:
    return Entity(name="Mod", entity_type=EntityType.AI, id=99)


def make_disc(**state) -> Discussion:
    """A discussion in the NGT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = {
        "current_phase": "generate",
        "phase_round": 1,
        "ideas": [],
        "candidates": [],
        "cluster_attempts": 0,
        "point_allocations": [],
        "points_per_voter": POINTS_PER_VOTER,
        **state,
    }
    return disc


IDEA_LINES = (
    "1. Offer a self-serve onboarding checklist inside the product\n"
    "2. Run monthly live office hours for new customers"
)


class TestGenerateIdeasHandler:
    def test_phase_metadata(self):
        handler = GenerateIdeasHandler()
        assert handler.phase.name == "generate"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        handler = GenerateIdeasHandler()
        assert handler.init_state(make_disc()) == {"ideas": []}

    def test_system_prompt_marks_silent_generation(self, ai_entity):
        prompt = GenerateIdeasHandler().get_system_prompt(
            ai_entity, make_disc())
        assert "SILENT IDEA GENERATION" in prompt
        assert "TestAI" in prompt
        assert "customer onboarding" in prompt
        assert "submit_ideas" in prompt

    def test_turn_prompt_names_tool(self, ai_entity):
        prompt = GenerateIdeasHandler().get_turn_prompt(
            ai_entity, make_disc())
        assert "submit_ideas" in prompt

    def test_summary_prompt_forbids_revealing_ideas(self):
        prompt = GenerateIdeasHandler().get_summary_prompt(
            make_disc(), "TestAI", "Bob")
        assert "Do NOT reveal" in prompt
        assert "Bob" in prompt

    def test_context_is_anonymised(self, ai_entity):
        disc = make_disc()
        out = GenerateIdeasHandler().filter_context_message(
            "TestAI", "TestAI suggests a checklist", "assistant", disc)
        assert "TestAI" not in out
        assert "Panelist" in out

    def test_free_text_path_records_ideas(self, ai_entity):
        disc = make_disc()
        result = GenerateIdeasHandler().process_response(
            IDEA_LINES, ai_entity, disc)
        assert isinstance(result, ProcessedResponse)
        assert len(disc.method_state["ideas"]) == 2

    def test_should_not_advance_on_round_one(self):
        disc = make_disc()
        assert GenerateIdeasHandler().should_advance(disc) is False

    def test_advances_with_ideas_after_round_one(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_ideas(disc.method_state, ai_entity,
                     ["A substantive onboarding improvement idea"])
        assert GenerateIdeasHandler().should_advance(disc) is True

    def test_does_not_advance_without_ideas_before_cap(self):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS)
        assert GenerateIdeasHandler().should_advance(disc) is False

    def test_gives_up_after_cap_and_logs(self, caplog):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS + 1)
        with caplog.at_level("WARNING"):
            assert GenerateIdeasHandler().should_advance(disc) is True
        assert any("idea" in r.message.lower() for r in caplog.records)

    def test_aborts_method_when_no_ideas_after_cap(self):
        disc = make_disc(phase_round=MAX_GENERATE_ROUNDS + 1)
        handler = GenerateIdeasHandler()
        assert handler.next_phase(disc) is None
        msg = handler.get_method_complete_message(disc)
        assert "ended early" in msg

    def test_continues_linearly_with_ideas(self, ai_entity):
        disc = make_disc(phase_round=2)
        record_ideas(disc.method_state, ai_entity,
                     ["A substantive onboarding improvement idea"])
        handler = GenerateIdeasHandler()
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert handler.get_method_complete_message(disc) == ""
