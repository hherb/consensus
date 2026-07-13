"""Structured-output conversion of the ACH evaluate_matrix phase (#23).

The forced ``submit_matrix_ratings`` tool replaces free-text JSON-matrix
parsing (``_parse_ratings``) for tool-capable models; the free-text path
(``process_response``) remains intact for human participants who type
prose. The ``ratings`` payload is keyed by hypothesis label (H1, H2, ...)
and evidence label (E<id>, ...) — the same convention ``_parse_ratings``
/ ``_format_rating_matrix`` and the majority-vote aggregation in
``ach.py`` use.
"""

from consensus.methods.phases.evaluate_matrix import (
    MATRIX_TOOL_PARAMETERS,
    RATING_SYMBOLS,
    EvaluateMatrixHandler,
    validate_matrix_payload,
)
from consensus.models import Discussion, Entity, EntityType

HYPOTHESES = ["Economic decline and hyperinflation", "Military overextension"]
EVIDENCE = [
    {"id": 1, "text": "GDP declined 40% between 200-400 AD", "source": "Maddison"},
    {"id": 2, "text": "Legions reduced from 33 to 20", "source": "Notitia"},
]

PAYLOAD = {
    "ratings": {
        "H1": {"E1": "+", "E2": "-"},
        "H2": {"E1": "0", "E2": "+"},
    },
    "reasoning": "H1/E2 is inconsistent because the legion drawdown "
                 "predates the worst inflation.",
}


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Why did the Roman Empire fall?",
                      discussion_method="ach")
    disc.method_state = {
        "current_phase": "evaluate",
        "phase_round": 1,
        "hypotheses": HYPOTHESES,
        "evidence": EVIDENCE,
        "matrix": {},
        **state,
    }
    return disc


class TestMatrixToolParameters:
    def test_schema_shape(self):
        assert MATRIX_TOOL_PARAMETERS["type"] == "object"
        assert set(MATRIX_TOOL_PARAMETERS["required"]) == {
            "ratings", "reasoning"}
        props = MATRIX_TOOL_PARAMETERS["properties"]
        assert props["ratings"]["type"] == "object"
        inner = props["ratings"]["additionalProperties"]
        assert inner["type"] == "object"
        rating_schema = inner["additionalProperties"]
        assert rating_schema["type"] == "string"
        assert set(rating_schema["enum"]) == set(RATING_SYMBOLS)
        assert props["reasoning"]["type"] == "string"

    def test_rating_symbols_match_downstream_aggregation(self):
        # ach.py's _aggregate_matrix votes dict only recognizes these
        # three symbols -- the schema enum must match exactly.
        assert RATING_SYMBOLS == ("+", "-", "0")


class TestValidateMatrixPayload:
    def test_valid(self):
        assert validate_matrix_payload(PAYLOAD, HYPOTHESES, EVIDENCE) == ""

    def test_missing_ratings_key_rejected(self):
        assert validate_matrix_payload(
            {"reasoning": "x"}, HYPOTHESES, EVIDENCE) != ""

    def test_ratings_not_a_dict_rejected(self):
        bad = {"ratings": "H1: +", "reasoning": "x"}
        assert validate_matrix_payload(bad, HYPOTHESES, EVIDENCE) != ""

    def test_empty_ratings_rejected(self):
        bad = {"ratings": {}, "reasoning": "x"}
        assert validate_matrix_payload(bad, HYPOTHESES, EVIDENCE) != ""

    def test_unknown_hypothesis_label_rejected(self):
        bad = {"ratings": {"H3": {"E1": "+"}}, "reasoning": "x"}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert "H3" in err
        # The error must list the valid label set
        assert "H1" in err and "H2" in err

    def test_verbatim_hypothesis_text_key_rejected(self):
        """Keys must be labels, not the hypothesis text itself."""
        bad = {"ratings": {HYPOTHESES[0]: {"E1": "+"}}, "reasoning": "x"}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert HYPOTHESES[0] in err

    def test_unknown_evidence_label_rejected(self):
        bad = {"ratings": {"H1": {"E9": "+"}}, "reasoning": "x"}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert "E9" in err
        assert "E1" in err and "E2" in err

    def test_non_dict_row_rejected(self):
        bad = {"ratings": {"H1": "not a dict"}, "reasoning": "x"}
        assert validate_matrix_payload(bad, HYPOTHESES, EVIDENCE) != ""

    def test_empty_row_rejected(self):
        bad = {"ratings": {"H1": {}}, "reasoning": "x"}
        assert validate_matrix_payload(bad, HYPOTHESES, EVIDENCE) != ""

    def test_invalid_rating_symbol_rejected(self):
        bad = {"ratings": {"H1": {"E1": "++"}}, "reasoning": "x"}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert err != ""

    def test_invalid_rating_symbol_names_the_pair(self):
        bad = {"ratings": {"H1": {"E1": "maybe"}}, "reasoning": "x"}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert "H1" in err and "E1" in err

    def test_partial_matrix_accepted(self):
        """Coverage may be partial -- _parse_ratings never requires
        every H/E pair to be present, so the structured path holds the
        same bar rather than a stricter one."""
        partial = {"ratings": {"H1": {"E1": "+"}}, "reasoning": "x"}
        assert validate_matrix_payload(partial, HYPOTHESES, EVIDENCE) == ""

    def test_single_hypothesis_subset_accepted(self):
        partial = {"ratings": {"H2": {"E1": "0", "E2": "+"}},
                   "reasoning": "x"}
        assert validate_matrix_payload(partial, HYPOTHESES, EVIDENCE) == ""

    def test_missing_reasoning_rejected(self):
        bad = {"ratings": PAYLOAD["ratings"]}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert "reasoning" in err.lower()

    def test_whitespace_only_reasoning_rejected(self):
        bad = {**PAYLOAD, "reasoning": "   \n\t "}
        err = validate_matrix_payload(bad, HYPOTHESES, EVIDENCE)
        assert "reasoning" in err.lower()


class TestEvaluateMatrixHandlerStructured:
    def test_requires_structured_output(self):
        assert EvaluateMatrixHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = EvaluateMatrixHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_matrix_ratings"
        assert spec.parameters is MATRIX_TOOL_PARAMETERS
        assert "H1" in spec.description and "H2" in spec.description
        assert "E1" in spec.description and "E2" in spec.description

    def test_get_output_tool_none_when_no_hypotheses(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion(hypotheses=[])
        assert handler.get_output_tool(_entity(), disc) is None

    def test_get_output_tool_none_when_no_evidence(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion(evidence=[])
        assert handler.get_output_tool(_entity(), disc) is None

    def test_validate_delegates_to_shared_function(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_process_structured_writes_matrix_state(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion()
        entity = _entity(eid=7)
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        assert disc.method_state["matrix"]["7"] == PAYLOAD["ratings"]
        assert "Rating Matrix" in processed.display_content
        assert PAYLOAD["reasoning"] in processed.display_content

    def test_process_structured_preserves_other_evaluators(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion(matrix={"1": {"H1": {"E1": "-"}}})
        handler.process_structured_response(PAYLOAD, _entity(eid=2), disc)
        assert disc.method_state["matrix"]["1"] == {"H1": {"E1": "-"}}
        assert disc.method_state["matrix"]["2"] == PAYLOAD["ratings"]

    def test_process_structured_matches_free_text_shape(self):
        """Structured and free-text paths must write the identical shape
        into state["matrix"][str(entity.id)] so aggregation (ach.py)
        and display (_format_rating_matrix) work regardless of path."""
        handler = EvaluateMatrixHandler()
        disc = _discussion()
        handler.process_response(
            '```json\n'
            '{"ratings": {"H1": {"E1": "+", "E2": "-"}, '
            '"H2": {"E1": "0", "E2": "+"}}}\n'
            '```',
            _entity(eid=3), disc,
        )
        free_text_shape = disc.method_state["matrix"]["3"]

        disc2 = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(eid=4), disc2)
        structured_shape = disc2.method_state["matrix"]["4"]

        assert free_text_shape == structured_shape

    def test_free_text_path_still_works_for_humans(self):
        handler = EvaluateMatrixHandler()
        disc = _discussion()
        content = (
            'Here are my ratings:\n'
            '```json\n'
            '{"ratings": {"H1": {"E1": "+", "E2": "-"}}}\n'
            '```\n'
        )
        handler.process_response(content, _entity(), disc)
        assert disc.method_state["matrix"]["1"] == {
            "H1": {"E1": "+", "E2": "-"}}


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool_and_keeps_rating_legend(self):
        handler = EvaluateMatrixHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_matrix_ratings" in prompt
        assert "+ (consistent)" in prompt
        assert "- (inconsistent)" in prompt
        assert "0 (neutral)" in prompt
        assert "JSON matrix" not in prompt

    def test_turn_prompt_names_tool(self):
        handler = EvaluateMatrixHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_matrix_ratings" in prompt

    def test_degenerate_matrix_prompts_do_not_name_missing_tool(self):
        """When hypotheses or evidence are empty get_output_tool
        returns None (no forced tool), so the prompts must not
        instruct calling a tool that is not offered (PR #39 review)."""
        handler = EvaluateMatrixHandler()
        entity = _entity()
        for state in ({"evidence": []}, {"hypotheses": []}):
            disc = _discussion(**state)
            assert handler.get_output_tool(entity, disc) is None
            assert "submit_matrix_ratings" not in handler.get_system_prompt(
                entity, disc)
            assert "submit_matrix_ratings" not in handler.get_turn_prompt(
                entity, disc)
