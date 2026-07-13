"""Structured-output conversion of the Adversarial Collaboration
define_criteria phase (#23).

The forced submit_criteria tool replaces free-text ``**C1:** ...`` /
``C1: ...`` / numbered-list parsing for tool-capable models; the regex
free-text path (``process_response``) remains intact for human
participants who type prose. The ``criteria`` payload is a flat array
of criterion strings, deduplicated against ``state["criteria"]`` by
exact membership — the same rule the free-text path uses.
"""

from consensus.methods.phases.define_criteria import (
    CRITERIA_TOOL_PARAMETERS,
    CRITERION_MIN_LENGTH,
    DefineCriteriaHandler,
    validate_criteria_payload,
)
from consensus.models import Discussion, Entity, EntityType

PAYLOAD = {
    "criteria": [
        "Productivity measured by output per hour worked",
        "Employee satisfaction surveys show improvement",
    ],
    "reasoning": ("These two criteria are measurable, observable, and "
                  "fair to both the remote and office positions."),
}


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Is remote work more productive?",
                      discussion_method="adversarial_collab")
    disc.method_state = {
        "current_phase": "criteria",
        "phase_round": 1,
        "positions": {"Alice": "Pro remote", "Bob": "Anti remote"},
        "criteria": [],
        **state,
    }
    return disc


class TestCriteriaToolParameters:
    def test_schema_shape(self):
        assert CRITERIA_TOOL_PARAMETERS["type"] == "object"
        assert set(CRITERIA_TOOL_PARAMETERS["required"]) == {
            "criteria", "reasoning"}
        props = CRITERIA_TOOL_PARAMETERS["properties"]
        assert props["criteria"]["type"] == "array"
        assert props["criteria"]["items"]["type"] == "string"
        assert props["reasoning"]["type"] == "string"


class TestValidateCriteriaPayload:
    def test_valid(self):
        assert validate_criteria_payload(PAYLOAD) == ""

    def test_missing_criteria_key_rejected(self):
        assert validate_criteria_payload({"reasoning": "x"}) != ""

    def test_criteria_not_a_list_rejected(self):
        bad = {"criteria": "a single string", "reasoning": "x"}
        assert validate_criteria_payload(bad) != ""

    def test_empty_criteria_rejected(self):
        bad = {"criteria": [], "reasoning": "x"}
        assert validate_criteria_payload(bad) != ""

    def test_short_criterion_rejected(self):
        bad = {"criteria": ["Short"], "reasoning": "x"}
        err = validate_criteria_payload(bad)
        assert err != ""

    def test_criterion_at_min_length_rejected(self):
        # Exactly CRITERION_MIN_LENGTH chars — must not pass (mirrors the
        # free-text filter's strict ">" comparison).
        bad = {"criteria": ["x" * CRITERION_MIN_LENGTH], "reasoning": "x"}
        assert validate_criteria_payload(bad) != ""

    def test_non_string_criterion_rejected(self):
        bad = {"criteria": [123456789012], "reasoning": "x"}
        assert validate_criteria_payload(bad) != ""

    def test_missing_reasoning_rejected(self):
        bad = {"criteria": PAYLOAD["criteria"]}
        err = validate_criteria_payload(bad)
        assert "reasoning" in err.lower()

    def test_whitespace_only_reasoning_rejected(self):
        bad = {**PAYLOAD, "reasoning": "   \n\t "}
        err = validate_criteria_payload(bad)
        assert "reasoning" in err.lower()


class TestDefineCriteriaHandlerStructured:
    def test_requires_structured_output(self):
        assert DefineCriteriaHandler().requires_structured_output is True

    def test_declares_output_tool(self):
        handler = DefineCriteriaHandler()
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_criteria"
        assert spec.parameters is CRITERIA_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_function(self):
        handler = DefineCriteriaHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""

    def test_process_structured_appends_new_criteria(self):
        handler = DefineCriteriaHandler()
        disc = _discussion()
        entity = _entity()
        processed = handler.process_structured_response(PAYLOAD, entity, disc)
        criteria = disc.method_state["criteria"]
        assert criteria == PAYLOAD["criteria"]
        # Numbered list, reasoning rendered first
        assert "1." in processed.display_content
        assert "2." in processed.display_content
        assert PAYLOAD["criteria"][0] in processed.display_content
        assert PAYLOAD["criteria"][1] in processed.display_content
        assert PAYLOAD["reasoning"] in processed.display_content
        assert (processed.display_content.index(PAYLOAD["reasoning"])
                < processed.display_content.index("1."))

    def test_process_structured_strips_trailing_period(self):
        """Parity with the regex path: _parse_criteria rstrips '.' so
        a structured 'X.' dedups against a human-parsed 'X' (PR #39
        review)."""
        handler = DefineCriteriaHandler()
        disc = _discussion(criteria=["Productivity measured per hour"])
        payload = {
            "criteria": ["Productivity measured per hour."],
            "reasoning": "Testing trailing-period normalization.",
        }
        handler.process_structured_response(payload, _entity(), disc)
        assert disc.method_state["criteria"] == [
            "Productivity measured per hour"]

    def test_process_structured_dedups_exact_membership(self):
        """Exact-membership dedup, same rule as the regex path."""
        handler = DefineCriteriaHandler()
        existing = [PAYLOAD["criteria"][0]]
        disc = _discussion(criteria=list(existing))
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        criteria = disc.method_state["criteria"]
        assert criteria.count(PAYLOAD["criteria"][0]) == 1
        assert PAYLOAD["criteria"][1] in criteria
        assert len(criteria) == 2

    def test_process_structured_preserves_prior_criteria(self):
        handler = DefineCriteriaHandler()
        disc = _discussion(criteria=["A pre-existing criterion here"])
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        criteria = disc.method_state["criteria"]
        assert "A pre-existing criterion here" in criteria
        assert len(criteria) == 3

    def test_free_text_path_still_works_for_humans(self):
        handler = DefineCriteriaHandler()
        disc = _discussion()
        content = (
            "**C1:** Productivity measured by output per hour\n"
            "  - If higher remotely → supports remote work\n"
            "  - If higher in office → supports office work"
        )
        handler.process_response(content, _entity(), disc)
        criteria = disc.method_state["criteria"]
        assert criteria
        assert "Productivity" in criteria[0]


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = DefineCriteriaHandler()
        entity = _entity()
        disc = _discussion()
        prompt = handler.get_system_prompt(entity, disc)
        assert "submit_criteria" in prompt
        assert entity.name in prompt

    def test_turn_prompt_names_tool_round_1(self):
        handler = DefineCriteriaHandler()
        entity = _entity()
        disc = _discussion(phase_round=1)
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_criteria" in prompt

    def test_turn_prompt_names_tool_round_2(self):
        handler = DefineCriteriaHandler()
        entity = _entity()
        disc = _discussion(phase_round=2)
        prompt = handler.get_turn_prompt(entity, disc)
        assert "submit_criteria" in prompt
