"""Structured-output conversion of the Delphi estimate/revise phases (#23)."""

from consensus.methods.phases._delphi_helpers import (
    validate_estimate_payload,
)
from consensus.methods.phases.estimate import EstimateHandler
from consensus.methods.phases.revise_delphi import ReviseDelphiHandler
from consensus.models import Discussion, Entity, EntityType


def _entity() -> Entity:
    return Entity(id=7, name="Alice", entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="t", discussion_method="delphi")
    disc.method_state = {"current_phase": phase, "phase_round": 1,
                         "estimates": [], **state}
    return disc


PAYLOAD = {"estimate": 0.8, "confidence": "high", "unit": "probability",
           "reasoning": "Because of X, Y and Z."}


class TestValidateEstimatePayload:
    def test_valid(self):
        assert validate_estimate_payload(PAYLOAD) == ""

    def test_non_numeric_estimate(self):
        bad = dict(PAYLOAD, estimate="lots")
        assert "estimate" in validate_estimate_payload(bad)

    def test_bad_confidence(self):
        bad = dict(PAYLOAD, confidence="SURE")
        assert "confidence" in validate_estimate_payload(bad)

    def test_empty_reasoning(self):
        bad = dict(PAYLOAD, reasoning="  ")
        assert "reasoning" in validate_estimate_payload(bad)


class TestEstimateHandlerStructured:
    def test_declares_output_tool(self):
        handler = EstimateHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion("estimate"))
        assert spec.name == "submit_estimate"
        assert "estimate" in spec.parameters["properties"]

    def test_process_structured_records_round_zero(self):
        handler = EstimateHandler()
        disc = _discussion("estimate")
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["round"] == 0
        assert entry["value"] == 0.8
        assert entry["confidence"] == "HIGH"
        assert "Because of X" in processed.display_content
        assert "**Estimate:** 0.8" in processed.display_content

    def test_free_text_path_still_works_for_humans(self):
        handler = EstimateHandler()
        disc = _discussion("estimate")
        content = ('```json\n{"estimate": 0.5, "confidence": "LOW", '
                   '"unit": "probability"}\n```\nReasoning here.')
        handler.process_response(content, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["value"] == 0.5


class TestReviseHandlerStructured:
    def test_records_current_revision_round(self):
        handler = ReviseDelphiHandler()
        disc = _discussion("revise", revise_round=1)
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["round"] == 2

    def test_validate_delegates_to_shared_helper(self):
        handler = ReviseDelphiHandler()
        disc = _discussion("revise")
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""
