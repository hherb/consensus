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
from consensus.methods.phases.clarify_ideas import ClarifyIdeasHandler
from consensus.methods.phases.cluster_ideas import ClusterIdeasHandler
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


class TestClusterIdeasHandler:
    def _disc_with_ideas(self, **state):
        disc = make_disc(current_phase="cluster", **state)
        record_ideas(disc.method_state,
                     Entity(name="TestAI", entity_type=EntityType.AI, id=1),
                     ["Offer a self-serve onboarding checklist inside "
                      "the product",
                      "Run monthly live office hours for new customers"])
        return disc

    def test_phase_is_condition_based(self):
        handler = ClusterIdeasHandler()
        assert handler.phase.name == "cluster"
        assert handler.phase.rounds == 0

    def test_moderator_only_turn_order(self):
        disc = self._disc_with_ideas()
        assert ClusterIdeasHandler().get_turn_order([1, 2], disc) == [99]

    def test_system_prompt_lists_raw_ideas(self, moderator):
        disc = self._disc_with_ideas()
        prompt = ClusterIdeasHandler().get_system_prompt(moderator, disc)
        assert "CLUSTERING PHASE" in prompt
        assert "Idea 1:" in prompt
        assert "onboarding checklist" in prompt

    def test_turn_prompt_names_tool_and_retry_variant(self, moderator):
        disc = self._disc_with_ideas()
        assert "submit_candidates" in ClusterIdeasHandler().get_turn_prompt(
            moderator, disc)
        disc.method_state["cluster_attempts"] = 1
        retry = ClusterIdeasHandler().get_turn_prompt(moderator, disc)
        assert "not usable" in retry

    def test_free_text_path_records_candidates(self, moderator):
        disc = self._disc_with_ideas()
        content = ("1. Build a self-serve onboarding checklist\n"
                   "2. Run recurring live office hours for customers")
        ClusterIdeasHandler().process_response(content, moderator, disc)
        assert len(disc.method_state["candidates"]) == 2
        assert disc.method_state["cluster_attempts"] == 0

    def test_unparseable_response_increments_attempts(self, moderator):
        disc = self._disc_with_ideas()
        ClusterIdeasHandler().process_response(
            "I think these all look great.", moderator, disc)
        assert disc.method_state["candidates"] == []
        assert disc.method_state["cluster_attempts"] == 1

    def test_advances_when_candidates_recorded(self):
        disc = self._disc_with_ideas()
        record_candidates(disc.method_state,
                          [{"title": "A consolidated candidate idea"}])
        assert ClusterIdeasHandler().should_advance(disc) is True

    def test_does_not_advance_without_candidates_before_cap(self):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS - 1)
        assert ClusterIdeasHandler().should_advance(disc) is False

    def test_gives_up_after_cap(self, caplog):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        with caplog.at_level("WARNING"):
            assert ClusterIdeasHandler().should_advance(disc) is True
        assert any("cluster" in r.message.lower() for r in caplog.records)

    def test_give_up_promotes_raw_ideas_to_candidates(self):
        disc = self._disc_with_ideas(cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        handler = ClusterIdeasHandler()
        assert handler.next_phase(disc) == LINEAR_NEXT
        candidates = disc.method_state["candidates"]
        assert len(candidates) == 2
        assert candidates[0]["title"] == disc.method_state["ideas"][0]["text"]

    def test_no_ideas_at_all_aborts(self):
        disc = make_disc(current_phase="cluster",
                         cluster_attempts=MAX_CLUSTER_ATTEMPTS)
        handler = ClusterIdeasHandler()
        assert handler.next_phase(disc) is None
        assert "ended early" in handler.get_method_complete_message(disc)

    def test_transition_message_counts_ideas(self):
        disc = self._disc_with_ideas()
        msg = ClusterIdeasHandler().get_transition_message(disc)
        assert "2 idea(s)" in msg


class TestClarifyIdeasHandler:
    def _disc(self):
        disc = make_disc(current_phase="clarify")
        record_candidates(disc.method_state, [
            {"title": "Build a self-serve onboarding checklist"},
            {"title": "Run recurring live office hours for customers"},
        ])
        return disc

    def test_phase_metadata(self):
        handler = ClarifyIdeasHandler()
        assert handler.phase.name == "clarify"
        assert handler.phase.rounds == 1
        assert handler.requires_structured_output is False

    def test_system_prompt_lists_candidates_and_forbids_ranking(
            self, ai_entity):
        prompt = ClarifyIdeasHandler().get_system_prompt(
            ai_entity, self._disc())
        assert "CLARIFICATION PHASE" in prompt
        assert "Candidate 1:" in prompt
        assert "Do NOT advocate" in prompt

    def test_turn_prompt(self, ai_entity):
        prompt = ClarifyIdeasHandler().get_turn_prompt(
            ai_entity, self._disc())
        assert "TestAI" in prompt
        assert "clarify" in prompt.lower()

    def test_context_is_anonymised(self):
        disc = self._disc()
        out = ClarifyIdeasHandler().filter_context_message(
            "TestAI", "TestAI asked about candidate 2", "assistant", disc)
        assert "TestAI" not in out

    def test_default_advancement_after_one_round(self):
        disc = self._disc()
        assert ClarifyIdeasHandler().should_advance(disc) is False
        disc.method_state["phase_round"] = 2
        assert ClarifyIdeasHandler().should_advance(disc) is True

    def test_transition_message_lists_candidates(self):
        msg = ClarifyIdeasHandler().get_transition_message(self._disc())
        assert "2 candidate idea(s)" in msg
        assert "Candidate 1:" in msg
