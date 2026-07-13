"""Structured-output conversion of Belief Diffusion prior/diffuse phases (#23).

The forced submit_beliefs tool replaces free-text JSON-block parsing for
tool-capable models; the regex free-text path (``process_response``)
remains intact for human participants who type prose.  The ``beliefs``
payload is keyed by hypothesis labels ("H1", "H2", ...) — the same
convention the free-text path and the display/convergence helpers use.
"""

from consensus.methods.phases._belief_helpers import (
    BELIEF_MAX,
    BELIEF_MIN,
    BELIEFS_TOOL_PARAMETERS,
    validate_beliefs_payload,
)
from consensus.methods.phases.diffuse_beliefs import DiffuseBeliefsHandler
from consensus.methods.phases.prior_beliefs import PriorBeliefsHandler
from consensus.models import Discussion, Entity, EntityType

HYPOTHESES = [
    "The effect is caused by mechanism A",
    "The effect is caused by mechanism B",
    "The effect is a measurement artifact",
]

PAYLOAD = {
    "beliefs": {"H1": 0.5, "H2": 0.3, "H3": 0.2},
    "reasoning": "Mechanism A best explains the observed pattern.",
}


def _entity(eid: int = 2, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="t", discussion_method="belief_diffusion")
    disc.method_state = {
        "current_phase": "prior",
        "phase_round": 1,
        "hypotheses": HYPOTHESES,
        "belief_history": [],
        "diffuse_round": 0,
        **state,
    }
    return disc


class TestBeliefsToolParameters:
    def test_value_schema_bounds_match_runtime_validation(self):
        """Schema-enforcing providers should constrain values to the
        same [0, 1] range validate_beliefs_payload enforces at runtime
        (PR #39 review)."""
        value_schema = BELIEFS_TOOL_PARAMETERS["properties"]["beliefs"][
            "additionalProperties"]
        assert value_schema["minimum"] == BELIEF_MIN
        assert value_schema["maximum"] == BELIEF_MAX


class TestValidateBeliefsPayload:
    def test_valid(self):
        assert validate_beliefs_payload(PAYLOAD, HYPOTHESES) == ""

    def test_missing_beliefs_key_rejected(self):
        assert validate_beliefs_payload({}, HYPOTHESES) != ""

    def test_empty_beliefs_rejected(self):
        assert validate_beliefs_payload({"beliefs": {}}, HYPOTHESES) != ""

    def test_unknown_label_rejected(self):
        bad = {"beliefs": {"H1": 0.3, "H2": 0.3, "H3": 0.2, "H4": 0.2}}
        err = validate_beliefs_payload(bad, HYPOTHESES)
        assert "H4" in err
        # The error must list the valid label set
        for label in ("H1", "H2", "H3"):
            assert label in err

    def test_verbatim_hypothesis_text_key_rejected(self):
        """Keys must be labels, not the hypothesis text itself."""
        bad = {"beliefs": {HYPOTHESES[0]: 0.5, "H2": 0.3, "H3": 0.2}}
        err = validate_beliefs_payload(bad, HYPOTHESES)
        assert HYPOTHESES[0] in err

    def test_incomplete_beliefs_rejected(self):
        bad = {"beliefs": {"H1": 0.5, "H2": 0.5}}
        err = validate_beliefs_payload(bad, HYPOTHESES)
        assert "H3" in err

    def test_out_of_range_value_rejected(self):
        bad = {"beliefs": {"H1": 1.5, "H2": 0.3, "H3": 0.2}}
        assert validate_beliefs_payload(bad, HYPOTHESES) != ""

    def test_negative_value_rejected(self):
        bad = {"beliefs": {"H1": -0.1, "H2": 0.5, "H3": 0.6}}
        assert validate_beliefs_payload(bad, HYPOTHESES) != ""

    def test_non_numeric_value_rejected(self):
        bad = {"beliefs": {"H1": "high", "H2": 0.5, "H3": 0.5}}
        assert validate_beliefs_payload(bad, HYPOTHESES) != ""

    def test_nan_value_rejected(self):
        bad = {"beliefs": {"H1": float("nan"), "H2": 0.5, "H3": 0.5}}
        assert validate_beliefs_payload(bad, HYPOTHESES) != ""

    def test_sum_far_from_one_rejected(self):
        """The prompt demands the distribution sum to 1.0; the retry
        loop is the cheap place to enforce it (PR #39 review)."""
        bad = {"beliefs": {"H1": 0.9, "H2": 0.9, "H3": 0.9}}
        err = validate_beliefs_payload(bad, HYPOTHESES)
        assert err != ""
        assert "sum" in err.lower()

    def test_sum_within_rounding_tolerance_accepted(self):
        """Two-decimal rounding (0.33 * 3 = 0.99) must not trigger a
        retry."""
        ok = {"beliefs": {"H1": 0.33, "H2": 0.33, "H3": 0.33}}
        assert validate_beliefs_payload(ok, HYPOTHESES) == ""


class TestPriorBeliefsHandlerStructured:
    def test_declares_output_tool(self):
        handler = PriorBeliefsHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_beliefs"
        # The description must name each label with its hypothesis text
        for i, h in enumerate(HYPOTHESES, 1):
            assert f"H{i}: {h}" in spec.description

    def test_get_output_tool_none_when_no_hypotheses(self):
        """Framing aborted with no hypotheses -> fall through to free text."""
        handler = PriorBeliefsHandler()
        disc = _discussion(hypotheses=[])
        assert handler.get_output_tool(_entity(), disc) is None

    def test_validate_delegates_to_shared_helper(self):
        handler = PriorBeliefsHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({"beliefs": {}}, _entity(), disc) != ""

    def test_process_structured_records_round_zero(self):
        handler = PriorBeliefsHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        history = disc.method_state["belief_history"]
        assert len(history) == 1
        entry = history[0]
        assert entry["round"] == 0
        assert entry["entity_id"] == entity.id
        assert entry["entity_name"] == entity.name
        assert entry["beliefs"] == PAYLOAD["beliefs"]
        assert "Belief Distribution" in processed.display_content
        # The bar chart maps H-labels back to the hypothesis text
        assert HYPOTHESES[0] in processed.display_content
        assert "Mechanism A best explains" in processed.display_content

    def test_free_text_path_still_works_for_humans(self):
        handler = PriorBeliefsHandler()
        disc = _discussion()
        content = '```json\n{"beliefs": {"H1": 0.6, "H2": 0.4}}\n```\nReasoning.'
        handler.process_response(content, _entity(), disc)
        [entry] = disc.method_state["belief_history"]
        assert entry["beliefs"] == {"H1": 0.6, "H2": 0.4}


class TestDiffuseBeliefsHandlerStructured:
    def test_declares_output_tool(self):
        handler = DiffuseBeliefsHandler()
        assert handler.requires_structured_output is True
        disc = _discussion(current_phase="diffuse")
        spec = handler.get_output_tool(_entity(), disc)
        assert spec.name == "submit_beliefs"
        for i, h in enumerate(HYPOTHESES, 1):
            assert f"H{i}: {h}" in spec.description

    def test_get_output_tool_none_when_no_hypotheses(self):
        handler = DiffuseBeliefsHandler()
        disc = _discussion(current_phase="diffuse", hypotheses=[])
        assert handler.get_output_tool(_entity(), disc) is None

    def test_validate_delegates_to_shared_helper(self):
        handler = DiffuseBeliefsHandler()
        disc = _discussion(current_phase="diffuse")
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_records_current_diffuse_round(self):
        handler = DiffuseBeliefsHandler()
        disc = _discussion(current_phase="diffuse", diffuse_round=2)
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        history = disc.method_state["belief_history"]
        assert len(history) == 1
        entry = history[0]
        assert entry["round"] == 3  # diffuse_round + 1
        assert entry["entity_id"] == entity.id
        assert entry["beliefs"] == PAYLOAD["beliefs"]
        assert "Belief Distribution" in processed.display_content

    def test_structured_and_free_text_entries_share_key_format(self):
        """A structured turn and a fallback free-text turn must produce
        comparable belief_history keys, or convergence detection breaks
        when an entity switches paths mid-diffusion."""
        handler = DiffuseBeliefsHandler()
        disc = _discussion(current_phase="diffuse", diffuse_round=0)
        handler.process_structured_response(PAYLOAD, _entity(1, "A"), disc)
        content = ('```json\n{"beliefs": {"H1": 0.5, "H2": 0.3, "H3": 0.2}}'
                   '\n```\nProse.')
        handler.process_response(content, _entity(2, "B"), disc)
        first, second = disc.method_state["belief_history"]
        assert set(first["beliefs"]) == set(second["beliefs"])

    def test_free_text_path_still_works_for_humans(self):
        handler = DiffuseBeliefsHandler()
        disc = _discussion(current_phase="diffuse", diffuse_round=1)
        content = '```json\n{"beliefs": {"H1": 0.7, "H2": 0.3}}\n```\nUpdated.'
        handler.process_response(content, _entity(), disc)
        [entry] = disc.method_state["belief_history"]
        assert entry["round"] == 2
        assert entry["beliefs"] == {"H1": 0.7, "H2": 0.3}
