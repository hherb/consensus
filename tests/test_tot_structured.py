"""Structured-output coverage for the ToT phases (#23 convention).

The forced submit_thoughts / submit_thought_scores / submit_expansions
tools replace free-text parsing for tool-capable models; the free-text
paths remain for humans.  Mirrors test_ngt_structured.
"""

from consensus.methods.phases._tot_helpers import (
    THOUGHTS_TOOL_PARAMETERS,
    record_thoughts,
)
from consensus.methods.phases.propose_thoughts import ProposeThoughtsHandler
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
