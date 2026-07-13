"""Structured-output conversion of the ACH hypothesize phase (#23).

The forced submit_hypotheses tool replaces free-text numbered-list
parsing for tool-capable models; the regex free-text path
(``process_response``) remains intact for human participants who type
prose. Unlike Belief Diffusion's own ``submit_hypotheses`` tool (which
frames a bounded 3-5 hypothesis set once), ACH accumulates hypotheses
across participants over multiple rounds — so this spec has no
min/max item-count bound. The ``hypotheses`` payload is a flat array
of hypothesis strings, deduplicated against ``state["hypotheses"]`` by
word-overlap similarity — the same rule the free-text path uses.
"""

from consensus.methods.phases.hypothesize import (
    HYPOTHESES_TOOL_PARAMETERS,
    MIN_HYPOTHESIS_LENGTH,
    HypothesizeHandler,
    validate_hypotheses_payload,
)
from consensus.models import Discussion, Entity, EntityType

PAYLOAD = {
    "hypotheses": [
        "Economic decline and hyperinflation weakened the empire",
        "Military overextension made the borders indefensible",
    ],
    "reasoning": ("These two causes are the most commonly cited "
                  "structural explanations and are worth evaluating "
                  "against each other."),
}


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Why did the Roman Empire fall?",
                      discussion_method="ach")
    disc.method_state = {
        "current_phase": "hypothesize",
        "phase_round": 1,
        "hypotheses": [],
        **state,
    }
    return disc


class TestHypothesesToolParameters:
    def test_schema_shape(self):
        assert HYPOTHESES_TOOL_PARAMETERS["type"] == "object"
        assert set(HYPOTHESES_TOOL_PARAMETERS["required"]) == {
            "hypotheses", "reasoning"}
        props = HYPOTHESES_TOOL_PARAMETERS["properties"]
        assert props["hypotheses"]["type"] == "array"
        assert props["hypotheses"]["items"]["type"] == "string"
        assert props["reasoning"]["type"] == "string"

    def test_no_min_max_items_bound(self):
        """ACH accumulates across participants — unlike Belief Diffusion's
        framing tool, there is no fixed 3-5 count bound."""
        props = HYPOTHESES_TOOL_PARAMETERS["properties"]
        assert "minItems" not in props["hypotheses"]
        assert "maxItems" not in props["hypotheses"]


class TestValidateHypothesesPayload:
    def test_valid(self):
        assert validate_hypotheses_payload(PAYLOAD) == ""

    def test_missing_hypotheses_key_rejected(self):
        assert validate_hypotheses_payload({"reasoning": "x"}) != ""

    def test_hypotheses_not_a_list_rejected(self):
        bad = {"hypotheses": "a single string", "reasoning": "x"}
        assert validate_hypotheses_payload(bad) != ""

    def test_empty_hypotheses_rejected(self):
        bad = {"hypotheses": [], "reasoning": "x"}
        assert validate_hypotheses_payload(bad) != ""

    def test_single_hypothesis_accepted(self):
        """ACH has no minimum count beyond 'at least one' — unlike
        Belief Diffusion framing, which requires 3-5."""
        ok = {"hypotheses": ["A substantive single hypothesis here"],
              "reasoning": "x"}
        assert validate_hypotheses_payload(ok) == ""

    def test_short_hypothesis_rejected(self):
        bad = {"hypotheses": ["Short"], "reasoning": "x"}
        assert validate_hypotheses_payload(bad) != ""

    def test_hypothesis_at_min_length_accepted(self):
        # Exactly MIN_HYPOTHESIS_LENGTH chars — mirrors parse_numbered_list's
        # inclusive ">=" filter used by the free-text path.
        ok = {"hypotheses": ["x" * MIN_HYPOTHESIS_LENGTH], "reasoning": "x"}
        assert validate_hypotheses_payload(ok) == ""

    def test_hypothesis_below_min_length_rejected(self):
        bad = {"hypotheses": ["x" * (MIN_HYPOTHESIS_LENGTH - 1)],
               "reasoning": "x"}
        assert validate_hypotheses_payload(bad) != ""

    def test_non_string_hypothesis_rejected(self):
        bad = {"hypotheses": [123456789012], "reasoning": "x"}
        assert validate_hypotheses_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        bad = {"hypotheses": PAYLOAD["hypotheses"]}
        err = validate_hypotheses_payload(bad)
        assert "reasoning" in err.lower()

    def test_whitespace_only_reasoning_rejected(self):
        bad = {**PAYLOAD, "reasoning": "   \n\t "}
        err = validate_hypotheses_payload(bad)
        assert "reasoning" in err.lower()


class TestHypothesizeHandlerStructured:
    def test_requires_structured_output(self):
        assert HypothesizeHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = HypothesizeHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_hypotheses"
        assert spec.parameters is HYPOTHESES_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_function(self):
        handler = HypothesizeHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_process_structured_appends_new_hypotheses(self):
        handler = HypothesizeHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        hypotheses = disc.method_state["hypotheses"]
        assert hypotheses == PAYLOAD["hypotheses"]
        assert "1." in processed.display_content
        assert "2." in processed.display_content
        assert PAYLOAD["hypotheses"][0] in processed.display_content
        assert PAYLOAD["hypotheses"][1] in processed.display_content
        assert PAYLOAD["reasoning"] in processed.display_content
        assert (processed.display_content.index(PAYLOAD["reasoning"])
                < processed.display_content.index("1."))

    def test_process_structured_dedups_by_word_overlap(self):
        """Near-duplicate wording must not be added, mirroring
        process_response's word_overlap_similar(threshold=0.7) rule."""
        handler = HypothesizeHandler()
        existing = ["Economic decline and hyperinflation weakened the empire"]
        disc = _discussion(hypotheses=list(existing))
        near_dup_payload = {
            "hypotheses": [
                "Economic decline and hyperinflation weakened the empire significantly",
                "Barbarian invasions overwhelmed the frontier defenses",
            ],
            "reasoning": "Testing dedup behavior across submissions.",
        }
        handler.process_structured_response(near_dup_payload, _entity(), disc)
        hypotheses = disc.method_state["hypotheses"]
        assert len(hypotheses) == 2
        assert "Barbarian invasions" in hypotheses[1]

    def test_process_structured_preserves_prior_hypotheses(self):
        handler = HypothesizeHandler()
        disc = _discussion(hypotheses=["A pre-existing hypothesis here"])
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        hypotheses = disc.method_state["hypotheses"]
        assert "A pre-existing hypothesis here" in hypotheses
        assert len(hypotheses) == 3

    def test_process_structured_accumulates_across_participants(self):
        """ACH's own semantics: hypotheses accumulate turn over turn,
        unlike Belief Diffusion's one-shot framing tool."""
        handler = HypothesizeHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(1, "Alice"), disc)
        second_payload = {
            "hypotheses": ["Barbarian invasions overwhelmed the frontier"],
            "reasoning": "A distinct, third explanation for the collapse.",
        }
        handler.process_structured_response(second_payload, _entity(2, "Bob"), disc)
        assert len(disc.method_state["hypotheses"]) == 3

    def test_free_text_path_still_works_for_humans(self):
        handler = HypothesizeHandler()
        disc = _discussion()
        content = (
            "1. Economic decline and hyperinflation weakened the empire\n"
            "2. Military overextension and inability to defend borders"
        )
        handler.process_response(content, _entity(), disc)
        hypotheses = disc.method_state["hypotheses"]
        assert len(hypotheses) == 2
        assert "Economic decline" in hypotheses[0]


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = HypothesizeHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_hypotheses" in prompt
        assert entity.name in prompt
        # Format-instruction wording must be gone in favor of the tool call.
        assert "Format each hypothesis on its own line" not in prompt

    def test_turn_prompt_names_tool(self):
        handler = HypothesizeHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_hypotheses" in prompt
