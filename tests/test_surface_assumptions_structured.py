"""Structured-output conversion of the Key Assumptions surface phase (#23).

The forced submit_assumptions tool replaces free-text numbered-list
parsing for tool-capable models; the regex free-text path
(``process_response``) remains intact for human participants who type
prose. The ``assumptions`` payload is a flat array of assumption
strings, deduplicated against ``state["assumptions"]`` by word-overlap
similarity — the same rule the free-text path uses.

Also covers the give-up cap fix: ``should_advance`` must return True
once ``phase_round`` exceeds ``MAX_SURFACE_ROUNDS``, regardless of
whether any assumptions have been parsed — mirroring hypothesize.py's
``MAX_HYPOTHESIZE_ROUNDS`` (issue #15 convention: parse-gated phases
must not loop forever).
"""

from consensus.methods.phases.surface_assumptions import (
    ASSUMPTIONS_TOOL_PARAMETERS,
    MAX_SURFACE_ROUNDS,
    MIN_ASSUMPTION_LENGTH,
    SurfaceAssumptionsHandler,
    validate_assumptions_payload,
)
from consensus.models import Discussion, Entity, EntityType

PAYLOAD = {
    "assumptions": [
        "The market will continue to grow at current rates",
        "Competitors will not significantly change their strategy",
    ],
    "reasoning": ("These are the load-bearing factual and causal "
                  "assumptions underlying the prevailing forecast."),
}


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Will AI replace programmers?",
                      discussion_method="key_assumptions")
    disc.method_state = {
        "current_phase": "surface",
        "phase_round": 1,
        "assumptions": [],
        **state,
    }
    return disc


class TestAssumptionsToolParameters:
    def test_schema_shape(self):
        assert ASSUMPTIONS_TOOL_PARAMETERS["type"] == "object"
        assert set(ASSUMPTIONS_TOOL_PARAMETERS["required"]) == {
            "assumptions", "reasoning"}
        props = ASSUMPTIONS_TOOL_PARAMETERS["properties"]
        assert props["assumptions"]["type"] == "array"
        assert props["assumptions"]["items"]["type"] == "string"
        assert props["reasoning"]["type"] == "string"


class TestValidateAssumptionsPayload:
    def test_valid(self):
        assert validate_assumptions_payload(PAYLOAD) == ""

    def test_missing_assumptions_key_rejected(self):
        assert validate_assumptions_payload({"reasoning": "x"}) != ""

    def test_assumptions_not_a_list_rejected(self):
        bad = {"assumptions": "a single string", "reasoning": "x"}
        assert validate_assumptions_payload(bad) != ""

    def test_empty_assumptions_rejected(self):
        bad = {"assumptions": [], "reasoning": "x"}
        assert validate_assumptions_payload(bad) != ""

    def test_single_assumption_accepted(self):
        ok = {"assumptions": ["A substantive single assumption here"],
              "reasoning": "x"}
        assert validate_assumptions_payload(ok) == ""

    def test_short_assumption_rejected(self):
        bad = {"assumptions": ["Short"], "reasoning": "x"}
        assert validate_assumptions_payload(bad) != ""

    def test_assumption_at_min_length_accepted(self):
        ok = {"assumptions": ["x" * MIN_ASSUMPTION_LENGTH], "reasoning": "x"}
        assert validate_assumptions_payload(ok) == ""

    def test_assumption_below_min_length_rejected(self):
        bad = {"assumptions": ["x" * (MIN_ASSUMPTION_LENGTH - 1)],
               "reasoning": "x"}
        assert validate_assumptions_payload(bad) != ""

    def test_non_string_assumption_rejected(self):
        bad = {"assumptions": [123456789012], "reasoning": "x"}
        assert validate_assumptions_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        bad = {"assumptions": PAYLOAD["assumptions"]}
        err = validate_assumptions_payload(bad)
        assert "reasoning" in err.lower()

    def test_whitespace_only_reasoning_rejected(self):
        bad = {**PAYLOAD, "reasoning": "   \n\t "}
        err = validate_assumptions_payload(bad)
        assert "reasoning" in err.lower()


class TestSurfaceAssumptionsHandlerStructured:
    def test_requires_structured_output(self):
        assert SurfaceAssumptionsHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = SurfaceAssumptionsHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_assumptions"
        assert spec.parameters is ASSUMPTIONS_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_function(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_process_structured_appends_new_assumptions(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        assumptions = disc.method_state["assumptions"]
        assert assumptions == PAYLOAD["assumptions"]
        assert "1." in processed.display_content
        assert "2." in processed.display_content
        assert PAYLOAD["assumptions"][0] in processed.display_content
        assert PAYLOAD["assumptions"][1] in processed.display_content
        assert PAYLOAD["reasoning"] in processed.display_content
        assert (processed.display_content.index(PAYLOAD["reasoning"])
                < processed.display_content.index("1."))

    def test_process_structured_dedups_by_word_overlap(self):
        """Near-duplicate wording must not be added, mirroring
        process_response's word_overlap_similar dedup rule."""
        handler = SurfaceAssumptionsHandler()
        existing = ["The market will continue to grow at current rates"]
        disc = _discussion(assumptions=list(existing))
        near_dup_payload = {
            "assumptions": [
                "The market will continue to grow at current rates steadily",
                "The regulatory environment will remain stable",
            ],
            "reasoning": "Testing dedup behavior across submissions.",
        }
        handler.process_structured_response(near_dup_payload, _entity(), disc)
        assumptions = disc.method_state["assumptions"]
        assert len(assumptions) == 2
        assert "regulatory environment" in assumptions[1]

    def test_process_structured_preserves_prior_assumptions(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion(assumptions=["A pre-existing assumption here"])
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        assumptions = disc.method_state["assumptions"]
        assert "A pre-existing assumption here" in assumptions
        assert len(assumptions) == 3

    def test_process_structured_accumulates_across_participants(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(1, "Alice"), disc)
        second_payload = {
            "assumptions": ["The regulatory environment will remain stable"],
            "reasoning": "A distinct, third assumption worth surfacing.",
        }
        handler.process_structured_response(second_payload, _entity(2, "Bob"), disc)
        assert len(disc.method_state["assumptions"]) == 3

    def test_free_text_path_still_works_for_humans(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion()
        content = (
            "1. The market will continue to grow at current rates\n"
            "2. Competitors will not significantly change their strategy"
        )
        handler.process_response(content, _entity(), disc)
        assumptions = disc.method_state["assumptions"]
        assert len(assumptions) == 2
        assert "market" in assumptions[0].lower()


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = SurfaceAssumptionsHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_assumptions" in prompt
        assert entity.name in prompt
        # Format-instruction wording must be gone in favor of the tool call.
        assert "Format each assumption as a numbered item" not in prompt

    def test_turn_prompt_names_tool(self):
        handler = SurfaceAssumptionsHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_assumptions" in prompt


class TestGiveUpCap:
    """Behavior fix: surface_assumptions must not loop forever when
    the group never surfaces parseable assumptions (mirrors
    hypothesize.py's MAX_HYPOTHESIZE_ROUNDS give-up cap, issue #15)."""

    def test_max_surface_rounds_is_three(self):
        assert MAX_SURFACE_ROUNDS == 3

    def test_does_not_advance_before_cap_without_assumptions(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion(phase_round=MAX_SURFACE_ROUNDS, assumptions=[])
        assert handler.should_advance(disc) is False

    def test_gives_up_after_cap_even_without_assumptions(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion(phase_round=MAX_SURFACE_ROUNDS + 1, assumptions=[])
        assert handler.should_advance(disc) is True

    def test_still_advances_normally_with_assumptions_before_cap(self):
        handler = SurfaceAssumptionsHandler()
        disc = _discussion(phase_round=2, assumptions=["Some assumption here"])
        assert handler.should_advance(disc) is True
