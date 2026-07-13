"""Structured-output conversion of the blind_evaluate phase (#23).

The forced submit_validity_scores tool replaces free-text
``[VALIDITY <id>: <n>]`` / ``[OVERALL: <n>]`` tag parsing for
tool-capable models; the regex free-text path (``process_response``)
remains intact for human participants who type prose. The structured
display content still embeds ``[VALIDITY id: n]`` tags so
``BlindEvaluateHandler.filter_context_message`` keeps recognising
evaluation messages as belonging to this phase.
"""

from consensus.methods.phases._distillation_helpers import (
    VALIDITY_TOOL_PARAMETERS,
    validate_validity_scores_payload,
)
from consensus.methods.phases.blind_evaluate import BlindEvaluateHandler
from consensus.models import Discussion, Entity, EntityType

SKELETON = {
    "premises": [{"id": "P1", "text": "Economic growth needs energy"}],
    "inferences": [{"id": "I1", "from": ["P1"],
                    "text": "Renewables can sustain growth"}],
    "conclusions": [{"id": "C1", "from": ["I1"],
                     "text": "The anti-renewable argument fails"}],
}

EVAL_ITEMS = ["I1", "C1"]

PAYLOAD = {
    "scores": [
        {"inference_id": "I1", "score": 4},
        {"inference_id": "C1", "score": 3},
    ],
    "overall": 4,
}


def _entity(eid: int = 1, name: str = "Analyst_1") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="t", discussion_method="self_distillation",
                      moderator_id=100)
    disc.method_state = {
        "current_phase": "blind_evaluate",
        "phase_round": 1,
        "skeleton": SKELETON,
        "validity_scores": {},
        "overall_scores": {},
        **state,
    }
    return disc


class TestValidateValidityScoresPayload:
    def test_valid(self):
        assert validate_validity_scores_payload(PAYLOAD, EVAL_ITEMS) == ""

    def test_missing_scores_key_rejected(self):
        assert validate_validity_scores_payload({"overall": 3}, EVAL_ITEMS) != ""

    def test_empty_scores_rejected(self):
        bad = {"scores": [], "overall": 3}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""

    def test_unknown_id_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 4},
                          {"inference_id": "C1", "score": 3},
                          {"inference_id": "X9", "score": 2}],
               "overall": 3}
        err = validate_validity_scores_payload(bad, EVAL_ITEMS)
        assert "X9" in err
        # The error must list the valid id set
        for item_id in EVAL_ITEMS:
            assert item_id in err

    def test_duplicate_id_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 4},
                          {"inference_id": "I1", "score": 2},
                          {"inference_id": "C1", "score": 3}],
               "overall": 3}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""

    def test_missing_id_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 4}],
               "overall": 3}
        err = validate_validity_scores_payload(bad, EVAL_ITEMS)
        assert "C1" in err

    def test_score_out_of_range_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 9},
                          {"inference_id": "C1", "score": 3}],
               "overall": 3}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""

    def test_score_zero_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 0},
                          {"inference_id": "C1", "score": 3}],
               "overall": 3}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""

    def test_non_numeric_score_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": "high"},
                          {"inference_id": "C1", "score": 3}],
               "overall": 3}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""

    def test_missing_overall_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 4},
                          {"inference_id": "C1", "score": 3}]}
        err = validate_validity_scores_payload(bad, EVAL_ITEMS)
        assert "overall" in err.lower()

    def test_overall_out_of_range_rejected(self):
        bad = {"scores": [{"inference_id": "I1", "score": 4},
                          {"inference_id": "C1", "score": 3}],
               "overall": 7}
        assert validate_validity_scores_payload(bad, EVAL_ITEMS) != ""


class TestValidityToolParameters:
    def test_schema_shape(self):
        assert VALIDITY_TOOL_PARAMETERS["type"] == "object"
        assert set(VALIDITY_TOOL_PARAMETERS["required"]) == {"scores", "overall"}
        props = VALIDITY_TOOL_PARAMETERS["properties"]
        assert props["scores"]["type"] == "array"
        item_props = props["scores"]["items"]["properties"]
        assert item_props["inference_id"]["type"] == "string"
        assert item_props["score"]["type"] == "integer"
        assert props["overall"]["type"] == "integer"


class TestBlindEvaluateHandlerStructured:
    def test_requires_structured_output(self):
        assert BlindEvaluateHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = BlindEvaluateHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_validity_scores"
        for item_id in EVAL_ITEMS:
            assert item_id in spec.description

    def test_get_output_tool_none_when_skeleton_missing(self):
        handler = BlindEvaluateHandler()
        disc = _discussion(skeleton=None)
        assert handler.get_output_tool(_entity(), disc) is None

    def test_get_output_tool_none_when_skeleton_empty(self):
        handler = BlindEvaluateHandler()
        disc = _discussion(skeleton={})
        assert handler.get_output_tool(_entity(), disc) is None

    def test_validate_delegates_to_shared_helper(self):
        handler = BlindEvaluateHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({"overall": 3}, _entity(), disc) != ""

    def test_process_structured_records_scores(self):
        handler = BlindEvaluateHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)

        vs = disc.method_state["validity_scores"]
        assert vs["I1"]["Analyst_1"] == 4
        assert vs["C1"]["Analyst_1"] == 3
        assert disc.method_state["overall_scores"]["Analyst_1"] == 4

        assert "[VALIDITY I1: 4]" in processed.display_content
        assert "[VALIDITY C1: 3]" in processed.display_content
        assert "[OVERALL: 4]" in processed.display_content
        assert "I1: 4/5" in processed.display_content
        assert "Overall: 4/5" in processed.display_content

    def test_process_structured_multiple_entities(self):
        handler = BlindEvaluateHandler()
        disc = _discussion()
        e1 = _entity(1, "Alice")
        e2 = _entity(2, "Bob")
        handler.process_structured_response(
            {"scores": [{"inference_id": "I1", "score": 5},
                       {"inference_id": "C1", "score": 4}],
             "overall": 5}, e1, disc)
        handler.process_structured_response(
            {"scores": [{"inference_id": "I1", "score": 2},
                       {"inference_id": "C1", "score": 2}],
             "overall": 2}, e2, disc)

        vs = disc.method_state["validity_scores"]
        assert vs["I1"] == {"Alice": 5, "Bob": 2}
        assert vs["C1"] == {"Alice": 4, "Bob": 2}
        os = disc.method_state["overall_scores"]
        assert os == {"Alice": 5, "Bob": 2}

    def test_structured_display_survives_blindness_filter(self):
        """CRITICAL: the structured display must still be recognised as
        an in-phase evaluation message by filter_context_message, or the
        blindness filter would blank out other evaluators' scores."""
        handler = BlindEvaluateHandler()
        disc = _discussion()
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)

        kept = handler.filter_context_message(
            "Analyst_1", processed.display_content, "user", disc)
        assert kept == processed.display_content
        assert kept != ""

    def test_free_text_path_still_works(self):
        """process_response (free-text tag parsing) stays intact."""
        handler = BlindEvaluateHandler()
        disc = _discussion()
        content = "I1 is strong. [VALIDITY I1: 4] [OVERALL: 3]"
        result = handler.process_response(content, _entity(), disc)
        vs = disc.method_state["validity_scores"]
        assert vs["I1"]["Analyst_1"] == 4
        assert disc.method_state["overall_scores"]["Analyst_1"] == 3
        assert "[VALIDITY" in result.display_content


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = BlindEvaluateHandler()
        entity = _entity()
        disc = _discussion()
        disc.method_state["skeleton_display"] = "**Premises:**\n- P1: x"
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_validity_scores" in prompt
        assert entity.name in prompt

    def test_turn_prompt_names_tool(self):
        handler = BlindEvaluateHandler()
        entity = _entity()
        disc = _discussion()
        disc.method_state["skeleton_display"] = "**Premises:**\n- P1: x"
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_validity_scores" in prompt
