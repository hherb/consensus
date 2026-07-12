"""Tests for the OutputToolSpec declaration and delegation hooks (issue #23)."""

from consensus.methods.base import (
    DiscussionMethod, OutputToolSpec, Phase, ProcessedResponse,
)
from consensus.methods.phase_handler import PhaseHandler
from consensus.models import Discussion


class _PlainHandler(PhaseHandler):
    phase = Phase("plain", "Plain")

    def get_system_prompt(self, entity, discussion) -> str:
        return ""

    def get_turn_prompt(self, entity, discussion) -> str:
        return ""


class _StructuredHandler(_PlainHandler):
    phase = Phase("structured", "Structured")
    requires_structured_output = True

    def get_output_tool(self, entity, discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_thing",
            description="Submit a thing.",
            parameters={"type": "object", "properties": {}},
        )

    def validate_output(self, payload, entity, discussion) -> str:
        return "" if payload.get("ok") else "'ok' must be truthy."

    def process_structured_response(self, payload, entity,
                                    discussion) -> ProcessedResponse:
        discussion.method_state["got"] = payload
        return ProcessedResponse(display_content="done")


class _HybridMethod(DiscussionMethod):
    name = "_test_structured_hooks"
    display_name = "Structured Hooks Test"
    description = "test"
    phase_handlers = (_PlainHandler(), _StructuredHandler())


def _discussion(phase: str) -> Discussion:
    disc = Discussion(topic="t", discussion_method="_test_structured_hooks")
    disc.method_state = {"current_phase": phase, "phase_round": 1}
    return disc


def test_spec_to_openai_schema():
    spec = OutputToolSpec("submit_x", "desc",
                          {"type": "object", "properties": {}})
    assert spec.to_openai_schema() == {
        "type": "function",
        "function": {"name": "submit_x", "description": "desc",
                     "parameters": {"type": "object", "properties": {}}},
    }


def test_default_handler_declares_no_output_tool():
    disc = _discussion("plain")
    assert _PlainHandler().get_output_tool(None, disc) is None
    assert _PlainHandler().validate_output({}, None, disc) == ""


def test_method_delegates_output_tool_to_active_handler():
    method = _HybridMethod()
    assert method.get_output_tool(None, _discussion("plain")) is None
    spec = method.get_output_tool(None, _discussion("structured"))
    assert spec is not None and spec.name == "submit_thing"


def test_method_delegates_validation_and_processing():
    method = _HybridMethod()
    disc = _discussion("structured")
    assert method.validate_output({"ok": True}, None, disc) == ""
    assert "'ok'" in method.validate_output({}, None, disc)
    processed = method.process_structured_response({"ok": True}, None, disc)
    assert processed.display_content == "done"
    assert disc.method_state["got"] == {"ok": True}


def test_requires_structured_output_flag():
    assert _HybridMethod().requires_structured_output() is True

    class _AllPlain(DiscussionMethod):
        name = "_test_all_plain"
        display_name = "Plain"
        description = "test"
        phase_handlers = (_PlainHandler(),)

    assert _AllPlain().requires_structured_output() is False


def test_default_process_structured_response_dumps_json():
    disc = _discussion("plain")
    processed = _PlainHandler().process_structured_response(
        {"a": 1}, None, disc)
    assert '"a": 1' in processed.display_content
