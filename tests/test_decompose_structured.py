"""Structured-output conversion of the Recursive Decomposition decompose
phase (#23).

The forced submit_subquestions tool replaces free-text numbered-list
parsing for tool-capable models; the regex free-text path
(``process_response``) remains intact for human participants who type
prose. The ``sub_questions`` payload is a flat array of sub-question
strings, deduplicated against ``state["sub_questions"]`` by word-overlap
similarity — the same rule the free-text path uses.

Also covers the give-up cap fix: ``should_advance`` must return True
once ``phase_round`` exceeds ``MAX_DECOMPOSE_ROUNDS``, regardless of
whether any sub-questions have been parsed — mirroring
surface_assumptions.py's ``MAX_SURFACE_ROUNDS`` (issue #15 convention:
parse-gated phases must not loop forever).
"""

from consensus.methods.phases.decompose import (
    DecomposeHandler,
    MAX_DECOMPOSE_ROUNDS,
    MIN_SUBQUESTION_LENGTH,
    SUBQUESTIONS_TOOL_PARAMETERS,
    validate_subquestions_payload,
)
from consensus.models import Discussion, Entity, EntityType

PAYLOAD = {
    "sub_questions": [
        "What are the physical mechanisms causing the sky to appear blue?",
        "How does atmospheric composition affect the perceived sky colour?",
    ],
    "reasoning": ("These sub-questions cover the physical and "
                  "compositional dimensions of the main question."),
}


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Why is the sky blue?",
                      discussion_method="recursive_decomposition")
    disc.method_state = {
        "current_phase": "decompose",
        "phase_round": 1,
        "sub_questions": [],
        **state,
    }
    return disc


class TestSubquestionsToolParameters:
    def test_schema_shape(self):
        assert SUBQUESTIONS_TOOL_PARAMETERS["type"] == "object"
        assert set(SUBQUESTIONS_TOOL_PARAMETERS["required"]) == {
            "sub_questions", "reasoning"}
        props = SUBQUESTIONS_TOOL_PARAMETERS["properties"]
        assert props["sub_questions"]["type"] == "array"
        assert props["sub_questions"]["items"]["type"] == "string"
        assert props["reasoning"]["type"] == "string"


class TestValidateSubquestionsPayload:
    def test_valid(self):
        assert validate_subquestions_payload(PAYLOAD) == ""

    def test_missing_sub_questions_key_rejected(self):
        assert validate_subquestions_payload({"reasoning": "x"}) != ""

    def test_sub_questions_not_a_list_rejected(self):
        bad = {"sub_questions": "a single string", "reasoning": "x"}
        assert validate_subquestions_payload(bad) != ""

    def test_empty_sub_questions_rejected(self):
        bad = {"sub_questions": [], "reasoning": "x"}
        assert validate_subquestions_payload(bad) != ""

    def test_single_sub_question_accepted(self):
        ok = {"sub_questions": ["A substantive single sub-question here"],
              "reasoning": "x"}
        assert validate_subquestions_payload(ok) == ""

    def test_short_sub_question_rejected(self):
        bad = {"sub_questions": ["Short"], "reasoning": "x"}
        assert validate_subquestions_payload(bad) != ""

    def test_sub_question_at_min_length_accepted(self):
        ok = {"sub_questions": ["x" * MIN_SUBQUESTION_LENGTH],
              "reasoning": "x"}
        assert validate_subquestions_payload(ok) == ""

    def test_sub_question_below_min_length_rejected(self):
        bad = {"sub_questions": ["x" * (MIN_SUBQUESTION_LENGTH - 1)],
               "reasoning": "x"}
        assert validate_subquestions_payload(bad) != ""

    def test_non_string_sub_question_rejected(self):
        bad = {"sub_questions": [123456789012], "reasoning": "x"}
        assert validate_subquestions_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        bad = {"sub_questions": PAYLOAD["sub_questions"]}
        err = validate_subquestions_payload(bad)
        assert "reasoning" in err.lower()

    def test_whitespace_only_reasoning_rejected(self):
        bad = {**PAYLOAD, "reasoning": "   \n\t "}
        err = validate_subquestions_payload(bad)
        assert "reasoning" in err.lower()


class TestDecomposeHandlerStructured:
    def test_requires_structured_output(self):
        assert DecomposeHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = DecomposeHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_subquestions"
        assert spec.parameters is SUBQUESTIONS_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_function(self):
        handler = DecomposeHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_process_structured_appends_new_sub_questions(self):
        handler = DecomposeHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        sub_questions = disc.method_state["sub_questions"]
        assert sub_questions == PAYLOAD["sub_questions"]
        assert "1." in processed.display_content
        assert "2." in processed.display_content
        assert PAYLOAD["sub_questions"][0] in processed.display_content
        assert PAYLOAD["sub_questions"][1] in processed.display_content
        assert PAYLOAD["reasoning"] in processed.display_content
        assert (processed.display_content.index(PAYLOAD["reasoning"])
                < processed.display_content.index("1."))

    def test_process_structured_dedups_by_word_overlap(self):
        """Near-duplicate wording must not be added, mirroring
        process_response's word_overlap_similar dedup rule."""
        handler = DecomposeHandler()
        existing = [
            "What are the physical mechanisms causing the sky to appear blue"
        ]
        disc = _discussion(sub_questions=list(existing))
        near_dup_payload = {
            "sub_questions": [
                "What are the physical mechanisms that make the sky appear blue",
                "How does altitude affect the perceived colour of the sky",
            ],
            "reasoning": "Testing dedup behavior across submissions.",
        }
        handler.process_structured_response(near_dup_payload, _entity(), disc)
        sub_questions = disc.method_state["sub_questions"]
        assert len(sub_questions) == 2
        assert "altitude" in sub_questions[1]

    def test_process_structured_preserves_prior_sub_questions(self):
        handler = DecomposeHandler()
        disc = _discussion(
            sub_questions=["A pre-existing sub-question here?"])
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        sub_questions = disc.method_state["sub_questions"]
        assert "A pre-existing sub-question here?" in sub_questions
        assert len(sub_questions) == 3

    def test_process_structured_accumulates_across_participants(self):
        handler = DecomposeHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(1, "Alice"), disc)
        second_payload = {
            "sub_questions": [
                "Why does the sky change colour at sunset and sunrise?"
            ],
            "reasoning": "A distinct, third sub-question worth exploring.",
        }
        handler.process_structured_response(second_payload, _entity(2, "Bob"),
                                            disc)
        assert len(disc.method_state["sub_questions"]) == 3

    def test_free_text_path_still_works_for_humans(self):
        handler = DecomposeHandler()
        disc = _discussion()
        content = (
            "1. What are the physical mechanisms causing the sky to appear blue?\n"
            "2. How does atmospheric composition affect sky colour?"
        )
        handler.process_response(content, _entity(), disc)
        sub_questions = disc.method_state["sub_questions"]
        assert len(sub_questions) == 2
        assert "physical mechanisms" in sub_questions[0].lower()


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = DecomposeHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_subquestions" in prompt
        assert entity.name in prompt
        # Format-instruction wording must be gone in favor of the tool call.
        assert "Format each sub-question on its own line" not in prompt

    def test_turn_prompt_names_tool(self):
        handler = DecomposeHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_subquestions" in prompt


class TestGiveUpCap:
    """Behavior fix: decompose must not loop forever when the group
    never proposes parseable sub-questions (mirrors
    surface_assumptions.py's MAX_SURFACE_ROUNDS give-up cap, issue #15)."""

    def test_max_decompose_rounds_is_three(self):
        assert MAX_DECOMPOSE_ROUNDS == 3

    def test_does_not_advance_before_cap_without_sub_questions(self):
        handler = DecomposeHandler()
        disc = _discussion(phase_round=MAX_DECOMPOSE_ROUNDS, sub_questions=[])
        assert handler.should_advance(disc) is False

    def test_gives_up_after_cap_even_without_sub_questions(self):
        handler = DecomposeHandler()
        disc = _discussion(phase_round=MAX_DECOMPOSE_ROUNDS + 1,
                           sub_questions=[])
        assert handler.should_advance(disc) is True

    def test_still_advances_normally_with_sub_questions_before_cap(self):
        handler = DecomposeHandler()
        disc = _discussion(phase_round=2,
                           sub_questions=["Some sub-question here?"])
        assert handler.should_advance(disc) is True
