"""Structured-output coverage for the NGT phases (#23 convention).

The forced submit_ideas / submit_candidates / submit_points tools
replace free-text parsing for tool-capable models; the free-text
paths remain for humans.  Mirrors test_surface_assumptions_structured.
"""

from consensus.methods import get_method
from consensus.methods.phases._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    CANDIDATES_TOOL_PARAMETERS,
    IDEAS_TOOL_PARAMETERS,
    POINTS_PER_VOTER,
    record_candidates,
)
from consensus.methods.phases.allocate_points import AllocatePointsHandler
from consensus.methods.phases.clarify_ideas import ClarifyIdeasHandler
from consensus.methods.phases.cluster_ideas import ClusterIdeasHandler
from consensus.methods.phases.generate_ideas import GenerateIdeasHandler
from consensus.methods.phases.rank_ideas import RankIdeasHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="How can we improve customer onboarding?",
                      discussion_method="nominal_group",
                      moderator_id=99)
    disc.method_state = get_method("nominal_group").init_state(disc)
    disc.method_state["current_phase"] = phase
    disc.method_state.update(state)
    return disc


class TestStructuredFlags:
    def test_generate_cluster_allocate_require_structured(self):
        assert GenerateIdeasHandler().requires_structured_output is True
        assert ClusterIdeasHandler().requires_structured_output is True
        assert AllocatePointsHandler().requires_structured_output is True

    def test_clarify_and_rank_do_not(self):
        assert ClarifyIdeasHandler().requires_structured_output is False
        assert RankIdeasHandler().requires_structured_output is False

    def test_method_requires_structured_output(self):
        assert (get_method("nominal_group").requires_structured_output()
                is True)


class TestOutputToolSpecs:
    def test_generate_spec(self):
        spec = GenerateIdeasHandler().get_output_tool(
            _entity(), _discussion("generate"))
        assert spec.name == "submit_ideas"
        assert spec.parameters is IDEAS_TOOL_PARAMETERS

    def test_cluster_spec(self):
        spec = ClusterIdeasHandler().get_output_tool(
            _entity(99, "Mod"), _discussion("cluster"))
        assert spec.name == "submit_candidates"
        assert spec.parameters is CANDIDATES_TOOL_PARAMETERS

    def test_allocate_spec(self):
        disc = _discussion("allocate")
        record_candidates(disc.method_state,
                          [{"title": "A substantive candidate idea"}])
        spec = AllocatePointsHandler().get_output_tool(_entity(), disc)
        assert spec.name == "submit_points"
        assert spec.parameters is ALLOCATIONS_TOOL_PARAMETERS


class TestPromptsNameTheTool:
    def test_generate_prompts(self):
        handler = GenerateIdeasHandler()
        disc = _discussion("generate")
        assert "submit_ideas" in handler.get_system_prompt(_entity(), disc)
        assert "submit_ideas" in handler.get_turn_prompt(_entity(), disc)

    def test_cluster_turn_prompt(self):
        handler = ClusterIdeasHandler()
        disc = _discussion("cluster")
        assert "submit_candidates" in handler.get_turn_prompt(
            _entity(99, "Mod"), disc)

    def test_allocate_prompts(self):
        handler = AllocatePointsHandler()
        disc = _discussion("allocate")
        record_candidates(disc.method_state,
                          [{"title": "A substantive candidate idea"}])
        assert "submit_points" in handler.get_system_prompt(_entity(), disc)
        assert "submit_points" in handler.get_turn_prompt(_entity(), disc)


class TestStructuredMatchesFreeTextPaths:
    def test_generate_structured_and_free_text_produce_same_state(self):
        texts = ["Offer a self-serve onboarding checklist inside the "
                 "product",
                 "Run monthly live office hours for new customers"]
        handler = GenerateIdeasHandler()

        disc_a = _discussion("generate")
        handler.process_structured_response(
            {"ideas": texts, "reasoning": "Coverage of both modes."},
            _entity(), disc_a)

        disc_b = _discussion("generate")
        handler.process_response(
            "1. " + texts[0] + "\n2. " + texts[1], _entity(), disc_b)

        strip = [i["text"] for i in disc_a.method_state["ideas"]]
        assert strip == [i["text"] for i in disc_b.method_state["ideas"]]

    def test_cluster_structured_and_free_text_produce_same_state(self):
        handler = ClusterIdeasHandler()
        title = "Build a self-serve onboarding checklist"

        disc_a = _discussion("cluster")
        handler.process_structured_response(
            {"candidates": [{"title": title}],
             "reasoning": "Merged the self-serve ideas."},
            _entity(99, "Mod"), disc_a)

        disc_b = _discussion("cluster")
        handler.process_response("1. " + title, _entity(99, "Mod"), disc_b)

        assert (disc_a.method_state["candidates"]
                == disc_b.method_state["candidates"])

    def test_allocate_structured_and_free_text_produce_same_state(self):
        handler = AllocatePointsHandler()

        def fresh() -> Discussion:
            disc = _discussion("allocate")
            record_candidates(disc.method_state, [
                {"title": "A substantive candidate idea"},
                {"title": "Another substantive candidate idea"},
            ])
            return disc

        disc_a = fresh()
        handler.process_structured_response(
            {"allocations": [
                {"candidate_id": 1, "points": POINTS_PER_VOTER - 4},
                {"candidate_id": 2, "points": 4}],
             "reasoning": "Prioritising the first candidate."},
            _entity(), disc_a)

        disc_b = fresh()
        handler.process_response(
            f"Candidate 1: {POINTS_PER_VOTER - 4} points\n"
            "Candidate 2: 4 points",
            _entity(), disc_b)

        def key(state: dict) -> list[tuple]:
            return [(r["candidate_id"], r["points"], r["entity_id"])
                    for r in state["point_allocations"]]

        assert key(disc_a.method_state) == key(disc_b.method_state)
