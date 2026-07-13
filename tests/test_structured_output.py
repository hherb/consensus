"""Tests for the forced-tool-call turn generator (issue #23)."""

import json

import httpx
import pytest

from consensus.methods.base import OutputToolSpec
from consensus.models import AIConfig, Discussion, Entity, EntityType
from consensus.structured_output import (
    MAX_STRUCTURED_OUTPUT_ATTEMPTS,
    StructuredOutputError,
    generate_structured_turn,
)

SPEC = OutputToolSpec(
    name="submit_estimate",
    description="Submit your estimate.",
    parameters={"type": "object",
                "properties": {"estimate": {"type": "number"}},
                "required": ["estimate"]},
)


def _entity() -> Entity:
    return Entity(id=1, name="Alice", entity_type=EntityType.AI,
                  ai_config=AIConfig(provider_id=1, model="m",
                                     base_url="http://x", temperature=0.5,
                                     max_tokens=256))


class _Method:
    """Stub method: rejects payloads missing 'estimate'."""

    def validate_output(self, payload, entity, discussion) -> str:
        if "estimate" not in payload:
            return "'estimate' is required."
        return ""


def _tool_call(args: dict, name: str = "submit_estimate") -> dict:
    return {"id": "call_1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _result(message: dict) -> dict:
    return {"message": message, "finish_reason": "stop", "model": "m",
            "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "latency_ms": 7}


class _StubClient:
    """Scripted complete_with_tools; records calls."""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[dict] = []

    async def complete_with_tools(self, **kwargs):
        # Snapshot messages at call time — the generator mutates the list.
        recorded = dict(kwargs)
        recorded["messages"] = [dict(m) for m in kwargs.get("messages", [])]
        self.calls.append(recorded)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_400(body: str = "tools not supported") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/chat/completions")
    resp = httpx.Response(400, request=req, text=body)
    return httpx.HTTPStatusError("400", request=req, response=resp)


@pytest.mark.asyncio
async def test_valid_payload_first_try():
    client = _StubClient([_result(
        {"content": "", "tool_calls": [_tool_call({"estimate": 0.7})]})])
    entity = _entity()
    resp = await generate_structured_turn(
        client, entity.ai_config, entity, Discussion(topic="t"),
        [{"role": "user", "content": "go"}], SPEC, _Method())
    assert resp.structured_output == {"estimate": 0.7}
    assert resp.prompt_tokens == 10
    # Tool was forced
    assert client.calls[0]["tool_choice"] == {
        "type": "function", "function": {"name": "submit_estimate"}}


@pytest.mark.asyncio
async def test_validation_error_retries_with_feedback():
    client = _StubClient([
        _result({"content": "", "tool_calls": [_tool_call({})]}),
        _result({"content": "",
                 "tool_calls": [_tool_call({"estimate": 1.0})]}),
    ])
    entity = _entity()
    resp = await generate_structured_turn(
        client, entity.ai_config, entity, Discussion(topic="t"),
        [{"role": "user", "content": "go"}], SPEC, _Method())
    assert resp.structured_output == {"estimate": 1.0}
    # Usage accumulated over both attempts
    assert resp.prompt_tokens == 20
    # The retry conversation contains the validation error as a tool result
    second_messages = client.calls[1]["messages"]
    assert any(m.get("role") == "tool" and "'estimate' is required."
               in m.get("content", "") for m in second_messages)


@pytest.mark.asyncio
async def test_missing_tool_call_retries_as_user_feedback():
    client = _StubClient([
        _result({"content": "I think the answer is 0.7."}),
        _result({"content": "",
                 "tool_calls": [_tool_call({"estimate": 0.7})]}),
    ])
    entity = _entity()
    resp = await generate_structured_turn(
        client, entity.ai_config, entity, Discussion(topic="t"),
        [{"role": "user", "content": "go"}], SPEC, _Method())
    assert resp.structured_output == {"estimate": 0.7}
    second_messages = client.calls[1]["messages"]
    assert second_messages[-1]["role"] == "user"
    assert "submit_estimate" in second_messages[-1]["content"]


@pytest.mark.asyncio
async def test_attempts_exhausted_falls_back_with_warning():
    bad = {"content": "prose only", "tool_calls": [_tool_call({})]}
    client = _StubClient([_result(dict(bad, tool_calls=[_tool_call({})]))
                          for _ in range(MAX_STRUCTURED_OUTPUT_ATTEMPTS)])
    entity = _entity()
    resp = await generate_structured_turn(
        client, entity.ai_config, entity, Discussion(topic="t"),
        [{"role": "user", "content": "go"}], SPEC, _Method())
    assert resp.structured_output is None
    assert resp.warning
    assert resp.content == "prose only"
    assert len(client.calls) == MAX_STRUCTURED_OUTPUT_ATTEMPTS


@pytest.mark.asyncio
async def test_http_400_raises_structured_output_error():
    client = _StubClient([_http_400()])
    entity = _entity()
    with pytest.raises(StructuredOutputError):
        await generate_structured_turn(
            client, entity.ai_config, entity, Discussion(topic="t"),
            [{"role": "user", "content": "go"}], SPEC, _Method())


@pytest.mark.asyncio
async def test_http_400_error_includes_provider_detail():
    client = _StubClient([
        _http_400("Function calling is not enabled for this model")])
    entity = _entity()
    with pytest.raises(StructuredOutputError) as excinfo:
        await generate_structured_turn(
            client, entity.ai_config, entity, Discussion(topic="t"),
            [{"role": "user", "content": "go"}], SPEC, _Method())
    assert "Function calling is not enabled" in str(excinfo.value)


@pytest.mark.asyncio
async def test_http_400_unrelated_to_tools_is_reraised(caplog):
    """A 400 with a non-tool cause (e.g. context overflow) must not be
    misreported as missing tool support — and the provider body must be
    logged, since str(HTTPStatusError) does not include it."""
    client = _StubClient([_http_400(
        "This model's maximum context length is 8192 tokens")])
    entity = _entity()
    with pytest.raises(httpx.HTTPStatusError):
        await generate_structured_turn(
            client, entity.ai_config, entity, Discussion(topic="t"),
            [{"role": "user", "content": "go"}], SPEC, _Method())
    assert "maximum context length" in caplog.text


# ---------------------------------------------------------------------------
# Moderator integration (Task 4): structured phases route through the
# forced-tool path and skip registry tools.
# ---------------------------------------------------------------------------

import consensus.methods as methods_registry
from consensus.methods.base import DiscussionMethod, Phase, ProcessedResponse
from consensus.methods.phase_handler import PhaseHandler
from consensus.moderator import Moderator


class _EstimatePhase(PhaseHandler):
    phase = Phase("estimate", "Estimate")
    requires_structured_output = True

    def get_system_prompt(self, entity, discussion) -> str:
        return "sys"

    def get_turn_prompt(self, entity, discussion) -> str:
        return "turn"

    def get_output_tool(self, entity, discussion) -> OutputToolSpec:
        return SPEC

    def validate_output(self, payload, entity, discussion) -> str:
        return "" if "estimate" in payload else "'estimate' is required."

    def process_structured_response(self, payload, entity,
                                    discussion) -> ProcessedResponse:
        discussion.method_state.setdefault("estimates", []).append(
            {"entity_id": entity.id, "value": payload["estimate"]})
        return ProcessedResponse(
            display_content=f"Estimate: {payload['estimate']}")


class _StructuredTestMethod(DiscussionMethod):
    name = "_test_structured_turn"
    display_name = "Structured Turn Test"
    description = "test"
    phase_handlers = (_EstimatePhase(),)


@pytest.mark.asyncio
async def test_moderator_generate_turn_uses_structured_path(
        monkeypatch, tmp_db, discussion_with_entities):
    disc = discussion_with_entities
    disc.discussion_method = "_test_structured_turn"
    method = _StructuredTestMethod()
    disc.method_state = method.init_state(disc)
    monkeypatch.setitem(methods_registry._METHODS,
                        "_test_structured_turn", _StructuredTestMethod)

    moderator = Moderator(disc, tmp_db)
    ai_entity = next(e for e in disc.entities
                     if e.entity_type == EntityType.AI)
    stub = _StubClient([_result(
        {"content": "", "tool_calls": [_tool_call({"estimate": 0.4})]})])
    monkeypatch.setattr(moderator, "_get_client", lambda entity: stub)

    resp = await moderator.generate_turn(ai_entity)
    assert resp.structured_output == {"estimate": 0.4}
    # The forced tool was requested, and no registry tools were mixed in
    assert stub.calls[0]["tools"] == [SPEC.to_openai_schema()]


@pytest.mark.asyncio
async def test_dsml_tool_calls_get_user_feedback_not_tool_role():
    client = _StubClient([
        _result({"content": "thinking...", "_dsml_tool_calls": True,
                 "tool_calls": [_tool_call({})]}),
        _result({"content": "",
                 "tool_calls": [_tool_call({"estimate": 2.0})]}),
    ])
    entity = _entity()
    resp = await generate_structured_turn(
        client, entity.ai_config, entity, Discussion(topic="t"),
        [{"role": "user", "content": "go"}], SPEC, _Method())
    assert resp.structured_output == {"estimate": 2.0}
    second_messages = client.calls[1]["messages"]
    # DSML models must never see OpenAI tool_calls/tool-role messages
    assert all("tool_calls" not in m for m in second_messages)
    assert all(m.get("role") != "tool" for m in second_messages)
