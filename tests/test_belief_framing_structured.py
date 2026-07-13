"""Structured-output conversion of Belief Diffusion framing (#23).

The forced submit_hypotheses tool makes the #30 failure mode
(unparseable framing -> degenerate belief phases) effectively
impossible for tool-capable models; the existing abort machinery
remains as the last-resort containment.
"""

from consensus.methods.phases.frame_hypotheses import (
    FrameHypothesesHandler, MAX_FRAMING_ATTEMPTS,
)
from consensus.models import Discussion, Entity, EntityType


def _entity() -> Entity:
    return Entity(id=1, name="Mod", entity_type=EntityType.AI)


def _discussion() -> Discussion:
    disc = Discussion(topic="t", discussion_method="belief_diffusion")
    handler = FrameHypothesesHandler()
    disc.method_state = {"current_phase": "frame", "phase_round": 1,
                         **handler.init_state(disc)}
    return disc


PAYLOAD = {
    "hypotheses": [
        "The effect is caused by mechanism A",
        "The effect is caused by mechanism B",
        "The effect is a measurement artifact",
    ],
    "rationale": "These partition the plausible answer space.",
}


class TestFramingValidation:
    def test_valid(self):
        handler = FrameHypothesesHandler()
        assert handler.validate_output(PAYLOAD, _entity(),
                                       _discussion()) == ""

    def test_too_few_hypotheses(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": ["Only one sufficiently long hypothesis"]}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""

    def test_short_junk_hypotheses_rejected(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": ["a", "b", "c"]}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""

    def test_non_list_rejected(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": "H1, H2, H3"}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""


class TestFramingProcessing:
    def test_hypotheses_recorded_and_displayed(self):
        handler = FrameHypothesesHandler()
        disc = _discussion()
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        assert disc.method_state["hypotheses"] == PAYLOAD["hypotheses"]
        assert "1. The effect is caused by mechanism A" \
            in processed.display_content
        assert "partition the plausible answer space" \
            in processed.display_content
        # Framing succeeded -> phase advances
        assert handler.should_advance(disc) is True

    def test_declares_output_tool(self):
        handler = FrameHypothesesHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_hypotheses"

    def test_abort_machinery_untouched(self):
        handler = FrameHypothesesHandler()
        disc = _discussion()
        disc.method_state["framing_attempts"] = MAX_FRAMING_ATTEMPTS
        assert handler.should_advance(disc) is True
        assert handler.next_phase(disc) is None
