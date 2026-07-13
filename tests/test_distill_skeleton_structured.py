"""Structured-output conversion of the distill_skeleton phase (#23).

The forced submit_skeleton tool replaces free-text ```json``` skeleton
parsing for tool-capable models; the regex/JSON-block free-text path
(``process_response``) remains intact for models that fall back to it
after exhausting the structured-output retry budget. This is a
moderator-only turn phase (``get_turn_order`` always returns just the
moderator id) and the ``MAX_EXTRACTION_ATTEMPTS`` give-up logic
(``extraction_attempts`` / ``extraction_failed`` / ``should_advance``)
is shared by both paths.
"""

from consensus.methods.phases._distillation_helpers import (
    SKELETON_TOOL_PARAMETERS,
    validate_skeleton_payload,
)
from consensus.methods.phases.distill_skeleton import DistillSkeletonHandler
from consensus.models import Discussion, Entity, EntityType

VALID_SKELETON = {
    "premises": [
        {"id": "P1", "text": "Economic growth correlates with energy consumption"},
        {"id": "P2", "text": "Renewable energy costs have declined 90% since 2010"},
    ],
    "inferences": [
        {"id": "I1", "from": ["P1", "P2"],
         "text": "Renewable energy can sustain economic growth at lower cost"},
    ],
    "conclusions": [
        {"id": "C1", "from": ["I1"],
         "text": "The economic argument against renewable transition is unsound"},
    ],
}

PAYLOAD = {
    **VALID_SKELETON,
    "rich_summary": (
        "The discussion leaned heavily on the Titanic analogy and an "
        "appeal to expert authority."
    ),
}


def _moderator(eid: int = 100, name: str = "Moderator") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Should we transition to renewables?",
                      discussion_method="self_distillation",
                      moderator_id=100)
    disc.method_state = {
        "current_phase": "distill",
        "phase_round": 1,
        "skeleton": None,
        "skeleton_display": "",
        "extraction_attempts": 0,
        "extraction_failed": False,
        **state,
    }
    return disc


class TestSkeletonToolParameters:
    def test_schema_shape(self):
        assert SKELETON_TOOL_PARAMETERS["type"] == "object"
        assert set(SKELETON_TOOL_PARAMETERS["required"]) == {
            "premises", "inferences", "conclusions", "rich_summary"}
        props = SKELETON_TOOL_PARAMETERS["properties"]
        assert props["premises"]["type"] == "array"
        assert props["inferences"]["type"] == "array"
        assert props["conclusions"]["type"] == "array"
        assert props["rich_summary"]["type"] == "string"

        premise_props = props["premises"]["items"]["properties"]
        assert set(premise_props) == {"id", "text"}

        inf_props = props["inferences"]["items"]["properties"]
        assert set(inf_props) >= {"id", "from", "text"}
        assert inf_props["from"]["type"] == "array"
        assert "from" in props["inferences"]["items"]["required"]

        con_props = props["conclusions"]["items"]["properties"]
        assert set(con_props) >= {"id", "from", "text"}
        assert "from" in props["conclusions"]["items"]["required"]


class TestValidateSkeletonPayload:
    def test_valid(self):
        assert validate_skeleton_payload(PAYLOAD) == ""

    def test_missing_key_rejected(self):
        bad = {k: v for k, v in PAYLOAD.items() if k != "inferences"}
        assert validate_skeleton_payload(bad) != ""

    def test_empty_premises_rejected(self):
        bad = {**PAYLOAD, "premises": []}
        assert validate_skeleton_payload(bad) != ""

    def test_missing_from_reference_rejected(self):
        bad = {
            **PAYLOAD,
            "inferences": [{"id": "I1", "from": ["NONEXISTENT"], "text": "x"}],
        }
        assert validate_skeleton_payload(bad) != ""

    def test_missing_rich_summary_rejected(self):
        bad = {k: v for k, v in PAYLOAD.items() if k != "rich_summary"}
        err = validate_skeleton_payload(bad)
        assert "rich_summary" in err.lower()

    def test_whitespace_only_rich_summary_rejected(self):
        bad = {**PAYLOAD, "rich_summary": "   \n\t "}
        err = validate_skeleton_payload(bad)
        assert "rich_summary" in err.lower()

    def test_empty_rich_summary_rejected(self):
        bad = {**PAYLOAD, "rich_summary": ""}
        err = validate_skeleton_payload(bad)
        assert "rich_summary" in err.lower()


class TestDistillSkeletonHandlerStructured:
    def test_requires_structured_output(self):
        assert DistillSkeletonHandler().requires_structured_output is True

    def test_turn_order_still_moderator_only(self):
        handler = DistillSkeletonHandler()
        disc = _discussion()
        assert handler.get_turn_order([1, 2, 3], disc) == [disc.moderator_id]

    def test_declares_output_tool(self):
        handler = DistillSkeletonHandler()
        spec = handler.get_output_tool(_moderator(), _discussion())
        assert spec.name == "submit_skeleton"
        assert spec.parameters is SKELETON_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_helper(self):
        handler = DistillSkeletonHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _moderator(), disc) == ""
        bad = {k: v for k, v in PAYLOAD.items() if k != "rich_summary"}
        assert handler.validate_output(bad, _moderator(), disc) != ""

    def test_process_structured_records_skeleton_and_display(self):
        handler = DistillSkeletonHandler()
        disc = _discussion()
        mod = _moderator()
        processed = handler.process_structured_response(PAYLOAD, mod, disc)

        state = disc.method_state
        assert state["skeleton"] == VALID_SKELETON
        assert state["skeleton_display"] != ""
        assert "P1" in state["skeleton_display"]
        assert state["extraction_failed"] is False
        assert state["rich_reasoning_summary"] == PAYLOAD["rich_summary"]

        # Display content matches what the free-text path shows: the
        # skeleton display plus the rich summary.
        assert "P1" in processed.display_content
        assert PAYLOAD["rich_summary"] in processed.display_content

    def test_process_structured_resets_extraction_failed(self):
        handler = DistillSkeletonHandler()
        disc = _discussion(extraction_failed=True, extraction_attempts=2)
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert disc.method_state["extraction_failed"] is False

    def test_process_structured_does_not_overwrite_existing_summary(self):
        """The rich summary is captured once, like the regex path."""
        handler = DistillSkeletonHandler()
        disc = _discussion(rich_reasoning_summary="already captured")
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert disc.method_state["rich_reasoning_summary"] == "already captured"

    def test_should_advance_after_structured_success(self):
        handler = DistillSkeletonHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert handler.should_advance(disc) is True

    def test_free_text_path_still_works(self):
        """process_response (free-text JSON-block parsing) stays intact."""
        import json
        handler = DistillSkeletonHandler()
        disc = _discussion()
        content = (
            "RICH SUMMARY: An appeal to expert authority.\n\n"
            f"```json\n{json.dumps(VALID_SKELETON)}\n```"
        )
        result = handler.process_response(content, _moderator(), disc)
        assert disc.method_state["skeleton"] == VALID_SKELETON
        assert disc.method_state["extraction_failed"] is False
        assert result.display_content == content


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = DistillSkeletonHandler()
        mod = _moderator()
        disc = _discussion()
        prompt = handler.get_system_prompt(mod, disc)
        assert "submit_skeleton" in prompt

    def test_initial_turn_prompt_names_tool(self):
        handler = DistillSkeletonHandler()
        mod = _moderator()
        disc = _discussion()
        prompt = handler.get_turn_prompt(mod, disc)
        assert "submit_skeleton" in prompt
        assert "premises" in prompt.lower()
        assert "inferences" in prompt.lower()

    def test_retry_turn_prompt_names_tool(self):
        handler = DistillSkeletonHandler()
        mod = _moderator()
        disc = _discussion(extraction_failed=True, extraction_attempts=1)
        prompt = handler.get_turn_prompt(mod, disc)
        assert "submit_skeleton" in prompt
        assert "try again" in prompt.lower() or "did not produce" in prompt.lower()

    def test_json_block_wording_removed_from_prompts(self):
        """The ```json code-block instructions are replaced by the
        tool-call instruction; the prose explaining premises/inferences
        /conclusions semantics is retained."""
        handler = DistillSkeletonHandler()
        mod = _moderator()
        disc = _discussion()
        initial = handler.get_turn_prompt(mod, disc)
        assert "```json" not in initial

        disc_retry = _discussion(extraction_failed=True, extraction_attempts=1)
        retry = handler.get_turn_prompt(mod, disc_retry)
        assert "```json" not in retry
