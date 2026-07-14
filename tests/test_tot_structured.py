"""Structured-output coverage for the ToT phases (#23 convention).

The forced submit_thoughts / submit_thought_scores / submit_expansions
tools replace free-text parsing for tool-capable models; the free-text
paths remain for humans.  Mirrors test_ngt_structured.
"""

from consensus.methods.phases._tot_helpers import (
    EXPANSIONS_TOOL_PARAMETERS,
    SCORES_TOOL_PARAMETERS,
    THOUGHTS_TOOL_PARAMETERS,
    record_thoughts,
)
from consensus.methods.phases.expand_thoughts import ExpandThoughtsHandler
from consensus.methods.phases.propose_thoughts import ProposeThoughtsHandler
from consensus.methods.phases.score_thoughts import ScoreThoughtsHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="How should we grow the developer community?",
                      discussion_method="tree_of_thoughts",
                      moderator_id=99)
    disc.method_state = {"current_phase": phase, "phase_round": 1,
                         "thoughts": [], "thought_scores": {},
                         "beam_history": [], "tot_artifact": {},
                         "expansions": []}
    disc.method_state.update(state)
    return disc


THOUGHTS_PAYLOAD = {
    "thoughts": [
        "Launch a public plugin marketplace with revenue sharing",
        "Run quarterly community hackathons with sponsored prizes",
    ],
    "reasoning": "One ecosystem play, one engagement play.",
}


class TestProposeStructured:
    def test_requires_structured_output(self):
        assert ProposeThoughtsHandler().requires_structured_output is True

    def test_output_tool_spec(self):
        spec = ProposeThoughtsHandler().get_output_tool(
            _entity(), _discussion("propose"))
        assert spec.name == "submit_thoughts"
        assert spec.parameters is THOUGHTS_TOOL_PARAMETERS

    def test_validate_output_delegates(self):
        handler = ProposeThoughtsHandler()
        disc = _discussion("propose")
        assert handler.validate_output(THOUGHTS_PAYLOAD, _entity(),
                                       disc) == ""
        assert handler.validate_output({"thoughts": [], "reasoning": "r"},
                                       _entity(), disc) != ""

    def test_process_structured_records_and_renders(self):
        handler = ProposeThoughtsHandler()
        disc = _discussion("propose")
        result = handler.process_structured_response(
            THOUGHTS_PAYLOAD, _entity(), disc)
        assert len(disc.method_state["thoughts"]) == 2
        assert "1." in result.display_content
        assert "ecosystem play" in result.display_content

    def test_duplicate_thoughts_not_rerendered(self):
        handler = ProposeThoughtsHandler()
        disc = _discussion("propose")
        record_thoughts(disc.method_state, _entity(2, "Bob"),
                        [THOUGHTS_PAYLOAD["thoughts"][0]])
        result = handler.process_structured_response(
            THOUGHTS_PAYLOAD, _entity(), disc)
        assert len(disc.method_state["thoughts"]) == 2
        assert "hackathons" in result.display_content


def _scored_discussion(**state) -> Discussion:
    disc = _discussion("score", **state)
    record_thoughts(disc.method_state, _entity(50, "Seed"),
                    THOUGHTS_PAYLOAD["thoughts"])
    return disc


SCORES_PAYLOAD = {
    "scores": {"T1": {"feasibility": 4, "impact": 5, "risk": 2},
               "T2": {"feasibility": 2, "impact": 3, "risk": 4}},
    "reasoning": "The marketplace compounds; hackathons fade.",
}


class TestScoreStructured:
    def test_requires_structured_output(self):
        assert ScoreThoughtsHandler().requires_structured_output is True

    def test_output_tool_spec_names_labels(self):
        disc = _scored_discussion()
        spec = ScoreThoughtsHandler().get_output_tool(_entity(), disc)
        assert spec.name == "submit_thought_scores"
        assert spec.parameters is SCORES_TOOL_PARAMETERS
        assert "T1" in spec.description and "T2" in spec.description

    def test_output_tool_none_without_thoughts(self):
        disc = _discussion("score")
        assert ScoreThoughtsHandler().get_output_tool(_entity(),
                                                      disc) is None

    def test_validate_output_restricts_to_eligible(self):
        disc = _scored_discussion()
        handler = ScoreThoughtsHandler()
        assert handler.validate_output(SCORES_PAYLOAD, _entity(),
                                       disc) == ""
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": [2], "ranking": []}]
        error = handler.validate_output(SCORES_PAYLOAD, _entity(), disc)
        assert "T1" in error

    def test_process_structured_records_and_renders(self):
        disc = _scored_discussion()
        result = ScoreThoughtsHandler().process_structured_response(
            SCORES_PAYLOAD, _entity(7, "Bob"), disc)
        assert disc.method_state["thought_scores"]["7"]["T2"] == {
            "feasibility": 2, "impact": 3, "risk": 4}
        assert "marketplace compounds" in result.display_content
        assert "T1" in result.display_content


def _beam_discussion() -> Discussion:
    disc = _discussion("expand")
    record_thoughts(disc.method_state, _entity(50, "Seed"),
                    THOUGHTS_PAYLOAD["thoughts"])
    disc.method_state["beam_history"] = [
        {"depth": 1, "beam_ids": [1],
         "ranking": [{"id": 1, "composite": 12.0, "scorer_count": 1}]}]
    return disc


EXPANSIONS_PAYLOAD = {
    "expansions": [
        {"thought_id": 1,
         "refinement": "Pilot with ten hand-picked launch partners",
         "obstacles": ["Payment-provider integration"]}],
    "reasoning": "The marketplace needs a de-risked first step.",
}


class TestExpandStructured:
    def test_requires_structured_output(self):
        assert ExpandThoughtsHandler().requires_structured_output is True

    def test_output_tool_spec_names_beam(self):
        disc = _beam_discussion()
        spec = ExpandThoughtsHandler().get_output_tool(_entity(), disc)
        assert spec.name == "submit_expansions"
        assert spec.parameters is EXPANSIONS_TOOL_PARAMETERS
        assert "T1" in spec.description

    def test_output_tool_none_without_beam_thoughts(self):
        disc = _discussion("expand")
        assert ExpandThoughtsHandler().get_output_tool(_entity(),
                                                       disc) is None

    def test_validate_output_restricts_to_beam(self):
        disc = _beam_discussion()
        handler = ExpandThoughtsHandler()
        assert handler.validate_output(EXPANSIONS_PAYLOAD, _entity(),
                                       disc) == ""
        bad = {"expansions": [{"thought_id": 2,
                               "refinement": "Long enough refinement"}],
               "reasoning": "r"}
        assert "2" in handler.validate_output(bad, _entity(), disc)

    def test_process_structured_records_with_depth(self):
        disc = _beam_discussion()
        result = ExpandThoughtsHandler().process_structured_response(
            EXPANSIONS_PAYLOAD, _entity(7, "Bob"), disc)
        recorded = disc.method_state["expansions"]
        assert len(recorded) == 1
        assert recorded[0]["depth"] == 1
        assert recorded[0]["entity_name"] == "Bob"
        assert "de-risked first step" in result.display_content
        assert "T1" in result.display_content
