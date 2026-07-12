# Structured Method Outputs via Native Function Calling (#23) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Method phases declare an output tool (e.g. `submit_estimate`) that the model is *forced* to call at the API layer, replacing regex-scraping of free text; validation errors trigger targeted retries, and non-tool-capable models are rejected at discussion setup.

**Architecture:** A new `OutputToolSpec` declared per `PhaseHandler` routes AI turns through a forced-tool-call path (`consensus/structured_output.py`) with bounded validation retries. Validated payloads flow to a new `process_structured_response` hook; the existing regex `process_response` path remains for human participants and as a loudly-warned containment fallback. A `supported_parameters` column in the pricing cache powers a setup-time capability check.

**Tech Stack:** Python 3.11+, httpx, SQLite, pytest + pytest-asyncio. No new dependencies (no jsonschema — semantic validation is hand-rolled per handler).

## Global Constraints

- `uv` only, never pip (CLAUDE.md).
- TDD: failing test first for every behavior change (docs/llm/golden_rules.md).
- Files under ~500 lines; docstrings + type hints mandatory; no magic numbers (named constants).
- Owner decision (issue #23 comment, 2026-07-12): tool-capable models may be *required* for structured methods; regex fallback need not remain first-class; surface a clear setup-time error, never a silent degrade.
- Human participants keep typing free text: the regex `process_response` path stays for them.
- Test flow behavior through the real pipeline (`complete_turn` / `generate_ai_turn` with mocked AI client), per HANDOVER.md conventions.
- Run the full suite (`uv run pytest tests/ -q`) before each commit.

---

### Task 1: `tool_choice` support in AIClient + `structured_output` on AIResponse

**Files:**
- Modify: `consensus/ai_client.py` (`AIResponse` dataclass ~line 108, `complete_with_tools` ~line 319)
- Test: `tests/test_ai_client.py`

**Interfaces:**
- Produces: `AIClient.complete_with_tools(messages, model, tools=None, tool_choice=None, temperature=0.7, max_tokens=1024) -> dict` — `tool_choice` (an OpenAI `{"type": "function", "function": {"name": ...}}` dict) is included in the request payload when set.
- Produces: `AIResponse.structured_output: Optional[dict] = None` field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_client.py`:

```python
class _JSONResponse:
    """Fake httpx response carrying a JSON body."""

    def __init__(self, data: dict) -> None:
        self._data = data
        self.status_code = 200
        self.text = ""
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        pass


def _chat_completion(message: dict) -> dict:
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "model": "m",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                  "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_complete_with_tools_sends_tool_choice(monkeypatch):
    client = AIClient(base_url="https://api.example.com")
    fake = _FakeClient([_JSONResponse(_chat_completion({"content": "hi"}))])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    choice = {"type": "function", "function": {"name": "submit_estimate"}}
    await client.complete_with_tools(
        messages=[], model="m",
        tools=[{"type": "function", "function": {"name": "submit_estimate"}}],
        tool_choice=choice,
    )
    assert fake.posts[0]["tool_choice"] == choice


@pytest.mark.asyncio
async def test_complete_with_tools_omits_tool_choice_by_default(monkeypatch):
    client = AIClient(base_url="https://api.example.com")
    fake = _FakeClient([_JSONResponse(_chat_completion({"content": "hi"}))])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    await client.complete_with_tools(messages=[], model="m")
    assert "tool_choice" not in fake.posts[0]


def test_airesponse_structured_output_defaults_to_none():
    from consensus.ai_client import AIResponse
    assert AIResponse(content="x").structured_output is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ai_client.py -q`
Expected: FAIL — `TypeError: complete_with_tools() got an unexpected keyword argument 'tool_choice'` and `AttributeError: ... structured_output`.

- [ ] **Step 3: Implement**

In `consensus/ai_client.py`, add to the `AIResponse` dataclass (after `warning: str = ""`):

```python
    structured_output: Optional[dict] = None  # forced-tool-call payload (issue #23)
```

Change `complete_with_tools` signature and payload:

```python
    async def complete_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict:
```

and after `if tools: payload["tools"] = tools` add:

```python
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
```

Extend the docstring: `tool_choice` forces the model to call a specific tool (OpenAI `{"type": "function", "function": {"name": ...}}` format).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ai_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_ai_client.py consensus/ai_client.py
git commit -m "feat(ai_client): tool_choice param and AIResponse.structured_output (#23)"
```

---

### Task 2: `OutputToolSpec` + handler and method hooks

**Files:**
- Modify: `consensus/methods/base.py` (add `OutputToolSpec` near `ProcessedResponse`; add delegation hooks to `DiscussionMethod`)
- Modify: `consensus/methods/phase_handler.py` (new hooks with defaults)
- Test: `tests/test_structured_output_hooks.py` (new)

**Interfaces:**
- Produces: `OutputToolSpec(name: str, description: str, parameters: dict)` dataclass with `to_openai_schema() -> dict` in `consensus/methods/base.py`.
- Produces: `PhaseHandler.requires_structured_output: ClassVar[bool] = False`; `PhaseHandler.get_output_tool(entity, discussion) -> OutputToolSpec | None` (default `None`); `PhaseHandler.validate_output(payload: dict, entity, discussion) -> str` (default `""` = valid); `PhaseHandler.process_structured_response(payload: dict, entity, discussion) -> ProcessedResponse` (default: JSON dump of payload).
- Produces: same three hooks on `DiscussionMethod` (delegating to the active handler) plus `DiscussionMethod.requires_structured_output() -> bool` (any handler flags it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_structured_output_hooks.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_output_hooks.py -q`
Expected: FAIL — `ImportError: cannot import name 'OutputToolSpec'`.

- [ ] **Step 3: Implement**

In `consensus/methods/base.py`, after the `ProcessedResponse` dataclass, add:

```python
@dataclass
class OutputToolSpec:
    """Declares the forced output tool for a structured phase (issue #23).

    Phases that declare a spec have their AI turns generated through a
    forced tool call instead of free-text parsing: the model must call
    ``name`` with arguments matching ``parameters`` (a JSON Schema
    object), making extraction near-deterministic.
    """

    name: str  # e.g. "submit_estimate"
    description: str  # shown to the model as the tool description
    parameters: dict  # JSON Schema for the tool arguments

    def to_openai_schema(self) -> dict:
        """Return the OpenAI function-tool schema for this spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

In `consensus/methods/phase_handler.py`, import `OutputToolSpec` from `.base`, add `import json` and, in the "Response processing" section of `PhaseHandler`:

```python
    #: True when this handler forces structured output via a declared
    #: output tool.  Read at setup time to require tool-capable models
    #: (issue #23) — handlers that set it must return a spec from
    #: ``get_output_tool``.
    requires_structured_output: ClassVar[bool] = False

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced output tool for this phase.

        Return an ``OutputToolSpec`` to have AI turns in this phase
        generated through a forced tool call (issue #23), or ``None``
        (default) for ordinary free-text turns.
        """
        return None

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Semantically validate a structured-output payload.

        Return ``""`` when the payload is acceptable, or a
        human-readable error the model can act on — the turn generator
        feeds it back and retries within the same conversation.
        Default: accept everything.
        """
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Handle a validated structured-output payload.

        Counterpart of ``process_response`` for the forced-tool path:
        write extracted data into ``discussion.method_state`` and build
        the display content.  Default: render the payload as JSON.
        """
        return ProcessedResponse(
            display_content=json.dumps(payload, indent=2))
```

In `consensus/methods/base.py`, add to `DiscussionMethod` (in the "Response post-processing" section), plus the `requires_structured_output` helper near `_handler_for_phase`:

```python
    def requires_structured_output(self) -> bool:
        """True when any phase of this method forces structured output.

        Read at discussion setup to require tool-capable models for all
        AI participants (issue #23, owner decision 2026-07-12).
        """
        return any(h.requires_structured_output for h in self.phase_handlers)

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> Optional[OutputToolSpec]:
        """Return the active phase's forced output tool, if any."""
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.get_output_tool(entity, discussion)
        return None

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Delegate payload validation to the active handler."""
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.validate_output(payload, entity, discussion)
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Delegate structured-payload processing to the active handler."""
        handler = self._active_handler(discussion)
        if handler is not None:
            return handler.process_structured_response(
                payload, entity, discussion)
        import json
        return ProcessedResponse(display_content=json.dumps(payload, indent=2))
```

(Put `import json` at module top of both files, not inline — shown inline here only for locality.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_structured_output_hooks.py tests/test_phase_machine_loops.py -q`
Expected: PASS (loops suite proves no regression in handler/method delegation).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/base.py consensus/methods/phase_handler.py tests/test_structured_output_hooks.py
git commit -m "feat(methods): OutputToolSpec and structured-output hooks (#23)"
```

---

### Task 3: forced-tool turn generator (`consensus/structured_output.py`)

**Files:**
- Create: `consensus/structured_output.py`
- Test: `tests/test_structured_output.py` (new)

**Interfaces:**
- Consumes: `AIClient.complete_with_tools(..., tool_choice=...)` (Task 1), `OutputToolSpec` + `method.validate_output` (Task 2).
- Produces: `MAX_STRUCTURED_OUTPUT_ATTEMPTS: int = 3`; `class StructuredOutputError(RuntimeError)`; `async generate_structured_turn(client, cfg, entity, discussion, messages, spec, method) -> AIResponse` where `cfg` is the entity's `AIConfig`. On success `AIResponse.structured_output` holds the validated payload dict; when attempts are exhausted it returns `structured_output=None` with a non-empty `warning` (containment fallback to the free-text path); an HTTP 400 raises `StructuredOutputError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_structured_output.py`:

```python
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
        self.calls.append(kwargs)
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_400() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/chat/completions")
    resp = httpx.Response(400, request=req, text="tools not supported")
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
    bad = _result({"content": "prose only",
                   "tool_calls": [_tool_call({})]})
    client = _StubClient([bad] * MAX_STRUCTURED_OUTPUT_ATTEMPTS)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_output.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.structured_output'`.

- [ ] **Step 3: Implement**

Create `consensus/structured_output.py`:

```python
"""Forced tool-call turn generation for structured method phases.

When a phase declares an ``OutputToolSpec``, AI turns in that phase are
generated here instead of through free-text completion: the model is
forced (via ``tool_choice``) to call the declared tool, the parsed
arguments are validated by the method's ``validate_output`` hook, and
validation errors are fed back for a bounded number of retries
(issue #23).

Failure containment: if the provider rejects the request outright
(HTTP 400 — typically a model without tool support) a
``StructuredOutputError`` is raised so the misconfiguration is loud,
never a silent degrade (owner decision 2026-07-12).  If a tool-capable
model keeps producing invalid payloads, the turn falls back to the
free-text path with a user-visible warning — the phase's own give-up
caps (e.g. ``MAX_FRAMING_ATTEMPTS``) then bound the damage.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

import httpx

from .ai_client import AIClient, AIResponse

if TYPE_CHECKING:
    from .methods.base import DiscussionMethod, OutputToolSpec
    from .models import AIConfig, Discussion, Entity

logger = logging.getLogger(__name__)

#: Bounded retries for invalid structured outputs before falling back
#: to the free-text parsing path.
MAX_STRUCTURED_OUTPUT_ATTEMPTS = 3


class StructuredOutputError(RuntimeError):
    """The provider/model cannot satisfy a phase's forced output tool."""


def _payload_from_tool_calls(
    tool_calls: list, tool_name: str,
) -> tuple[Optional[dict], str]:
    """Extract the declared tool's arguments from a tool_calls list.

    Returns ``(payload, "")`` on success or ``(None, error)`` when the
    tool was not called or its arguments were unusable.
    """
    for tc in tool_calls or []:
        func = tc.get("function", {})
        if func.get("name") != tool_name:
            continue
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            return None, f"The arguments to {tool_name} were not valid JSON."
        if not isinstance(args, dict):
            return None, f"The arguments to {tool_name} must be a JSON object."
        return args, ""
    return None, f"You must respond by calling the {tool_name} tool."


async def generate_structured_turn(
    client: AIClient,
    cfg: "AIConfig",
    entity: "Entity",
    discussion: "Discussion",
    messages: list[dict],
    spec: "OutputToolSpec",
    method: "DiscussionMethod",
) -> AIResponse:
    """Generate a turn whose output is a forced call to ``spec``.

    Args:
        client: The entity's AI client.
        cfg: The entity's AIConfig (model, temperature, max_tokens).
        entity: The participant taking the turn.
        discussion: The active discussion (for validation context).
        messages: The prepared OpenAI message array (mutated in place
            by retry feedback).
        spec: The phase's declared output tool.
        method: The active DiscussionMethod (validate_output hook).

    Returns:
        AIResponse with ``structured_output`` set to the validated
        payload, or — after exhausting retries — ``structured_output=None``
        plus a non-empty ``warning`` so the caller can fall back to
        free-text processing.

    Raises:
        StructuredOutputError: the provider rejected the forced tool
            call (HTTP 400), i.e. the model/provider lacks tool support.
    """
    tools = [spec.to_openai_schema()]
    tool_choice = {"type": "function", "function": {"name": spec.name}}
    prompt_tokens = completion_tokens = latency_ms = 0
    model_used = cfg.model
    last_content = ""

    for attempt in range(MAX_STRUCTURED_OUTPUT_ATTEMPTS):
        try:
            result = await client.complete_with_tools(
                messages=messages,
                model=cfg.model,
                tools=tools,
                tool_choice=tool_choice,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                raise StructuredOutputError(
                    f"{cfg.model} rejected the forced tool call required "
                    f"by this phase ({spec.name}). Assign a tool-capable "
                    f"model to {entity.name} or choose a method without "
                    "structured phases."
                ) from exc
            raise

        prompt_tokens += result["prompt_tokens"]
        completion_tokens += result["completion_tokens"]
        latency_ms += result["latency_ms"]
        model_used = result["model"]

        msg = result["message"]
        if (msg.get("content") or "").strip():
            last_content = msg["content"]
        is_dsml = msg.pop("_dsml_tool_calls", False)
        tool_calls = msg.get("tool_calls") or []

        payload, error = _payload_from_tool_calls(tool_calls, spec.name)
        if payload is not None:
            error = method.validate_output(payload, entity, discussion)
            if not error:
                return AIResponse(
                    content=msg.get("content") or "",
                    model=model_used,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    latency_ms=latency_ms,
                    structured_output=payload,
                )

        logger.warning(
            "Structured output attempt %d/%d rejected for %s (%s): %s",
            attempt + 1, MAX_STRUCTURED_OUTPUT_ATTEMPTS, entity.name,
            spec.name, error,
        )
        feedback = (
            f"Rejected: {error} Call {spec.name} again with corrected "
            "arguments."
        )
        if is_dsml or not tool_calls:
            # No OpenAI-format tool call to answer — DSML models don't
            # understand tool-role messages, and a call-less assistant
            # message cannot be followed by one.
            assistant_content = (msg.get("content")
                                 or f"(Calling {spec.name}...)")
            messages.append({"role": "assistant",
                             "content": assistant_content})
            messages.append({"role": "user", "content": feedback})
        else:
            messages.append(msg)
            # Every tool call needs a matching tool-role response.
            for tc in tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": feedback,
                })

    warning = (
        f"{entity.name}'s model could not produce a valid {spec.name} "
        f"output after {MAX_STRUCTURED_OUTPUT_ATTEMPTS} attempts — "
        "falling back to free-text parsing for this turn"
    )
    logger.warning("%s", warning)
    return AIResponse(
        content=last_content,
        model=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=latency_ms,
        structured_output=None,
        warning=warning,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_structured_output.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/structured_output.py tests/test_structured_output.py
git commit -m "feat: forced tool-call turn generator with bounded validation retries (#23)"
```

---

### Task 4: route structured turns through moderator and flow

**Files:**
- Modify: `consensus/moderator.py` (`generate_turn`, ~line 336 just before the tool-registry section)
- Modify: `consensus/app_discussion_flow.py` (`generate_ai_turn`, ~line 240)
- Test: `tests/test_structured_output.py` (extend), `tests/test_app_discussion_flow.py` (extend)

**Interfaces:**
- Consumes: `generate_structured_turn` (Task 3), `method.get_output_tool` / `method.process_structured_response` (Task 2).
- Produces: `Moderator.generate_turn` returns an `AIResponse` with `structured_output` set for structured phases; `generate_ai_turn` routes `structured_output` payloads to `method.process_structured_response` and everything else through the existing `method.process_response`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_structured_output.py`:

```python
class _StructuredPhaseHandler:
    """Duck-typed handler stand-ins are not enough here — use the real
    base classes so Moderator/flow delegation is exercised."""


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
```

Append to `tests/test_app_discussion_flow.py` (imports at top of file already include `AsyncMock`; the test mirrors `TestCostLimitEnforcement`'s mocking pattern — reuse its fixtures):

```python
class TestStructuredOutputRouting:
    """generate_ai_turn routes structured payloads to
    process_structured_response (issue #23)."""

    @pytest.mark.asyncio
    async def test_structured_payload_routed(
            self, monkeypatch, tmp_db, discussion_with_entities):
        import consensus.methods as methods_registry
        from consensus.ai_client import AIResponse
        from consensus.methods.base import (
            DiscussionMethod, Phase, ProcessedResponse,
        )
        from consensus.methods.phase_handler import PhaseHandler

        calls = {}

        class _Handler(PhaseHandler):
            phase = Phase("p", "P")
            requires_structured_output = True

            def get_system_prompt(self, entity, discussion):
                return ""

            def get_turn_prompt(self, entity, discussion):
                return ""

            def process_structured_response(self, payload, entity,
                                            discussion):
                calls["payload"] = payload
                return ProcessedResponse(display_content="structured!")

            def process_response(self, content, entity, discussion):
                calls["free_text"] = content
                return ProcessedResponse(display_content=content)

        class _M(DiscussionMethod):
            name = "_test_routing"
            display_name = "Routing"
            description = "test"
            phase_handlers = (_Handler(),)

        disc = discussion_with_entities
        disc.discussion_method = "_test_routing"
        disc.method_state = _M().init_state(disc)
        monkeypatch.setitem(methods_registry._METHODS,
                            "_test_routing", _M)

        moderator = Moderator(disc, tmp_db)
        moderator.generate_turn = AsyncMock(return_value=AIResponse(
            content="", structured_output={"estimate": 3}))

        result = await generate_ai_turn(
            disc, moderator, tmp_db, tmp_db.pricing)

        assert calls["payload"] == {"estimate": 3}
        assert "free_text" not in calls
        assert result["content"] == "structured!"
```

(Match the existing file's import style; add any missing imports — `generate_ai_turn`, `Moderator`, `pytest` are already imported there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_output.py tests/test_app_discussion_flow.py -q`
Expected: FAIL — the moderator test gets no `structured_output` (free-text path taken); the routing test finds `calls["free_text"]` set instead of `calls["payload"]`.

- [ ] **Step 3: Implement**

In `consensus/moderator.py`, add the import:

```python
from .structured_output import generate_structured_turn
```

In `generate_turn`, immediately after the `messages = ...` context building (before the `# Get tools for this entity` block):

```python
        # Structured-output phases (issue #23): force the declared
        # output tool instead of free-text parsing.  Registry tools are
        # not offered on these turns — the phase output is the task.
        output_spec = (method.get_output_tool(entity, self.discussion)
                       if method else None)
        if output_spec is not None:
            return await generate_structured_turn(
                client, cfg, entity, self.discussion, messages,
                output_spec, method)
```

In `consensus/app_discussion_flow.py` `generate_ai_turn`, replace:

```python
        method = get_active_method(discussion)
        if method and not passed:
            processed = method.process_response(
                resp.content, current, discussion)
            content = processed.display_content
```

with:

```python
        method = get_active_method(discussion)
        if method and not passed:
            if resp.structured_output is not None:
                # Forced-tool path (issue #23): the payload was already
                # validated by the method's validate_output hook.
                processed = method.process_structured_response(
                    resp.structured_output, current, discussion)
            else:
                processed = method.process_response(
                    resp.content, current, discussion)
            content = processed.display_content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_structured_output.py tests/test_app_discussion_flow.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest tests/ -q` — expected: all pass.

```bash
git add consensus/moderator.py consensus/app_discussion_flow.py tests/test_structured_output.py tests/test_app_discussion_flow.py
git commit -m "feat(flow): route structured phases through forced tool calls (#23)"
```

---

### Task 5: pricing cache learns `supported_parameters` + `supports_tools()`

**Files:**
- Create: `consensus/migrations/014_supported_parameters.sql`
- Modify: `consensus/pricing.py` (`refresh` ~line 89, `_lookup_row` SELECTs ~lines 217–258, new method after `get_context_length`)
- Test: `tests/test_pricing.py` (extend)

**Interfaces:**
- Produces: `PricingCache.supports_tools(model_name: str, base_url: str = "") -> Optional[bool]` — `True`/`False` when OpenRouter capability data exists, `None` when the model or its capability data is unknown (e.g. local models, pre-migration cache rows).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pricing.py` (reuse the file's existing fixture style; `tmp_db` comes from conftest):

```python
import time


def _insert_model(tmp_db, model_id: str, supported: str) -> None:
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    tmp_db.conn.commit()


class TestSupportsTools:
    def test_true_when_tools_listed(self, tmp_db):
        _insert_model(tmp_db, "openai/gpt-4o", "temperature,tools,top_p")
        assert tmp_db.pricing.supports_tools("gpt-4o") is True

    def test_false_when_tools_absent(self, tmp_db):
        _insert_model(tmp_db, "meta/plain-model", "temperature,top_p")
        assert tmp_db.pricing.supports_tools("plain-model") is False

    def test_none_when_capability_data_empty(self, tmp_db):
        _insert_model(tmp_db, "local/mystery-model", "")
        assert tmp_db.pricing.supports_tools("mystery-model") is None

    def test_none_for_unknown_model(self, tmp_db, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        assert tmp_db.pricing.supports_tools("no-such-model") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pricing.py -q`
Expected: FAIL — `sqlite3.OperationalError: table model_pricing has no column named supported_parameters` (or `AttributeError: supports_tools`).

- [ ] **Step 3: Implement**

Create `consensus/migrations/014_supported_parameters.sql`:

```sql
-- Tool-capability data from OpenRouter (issue #23): comma-separated
-- supported_parameters list; '' = unknown (row predates this column).
ALTER TABLE model_pricing ADD COLUMN supported_parameters TEXT DEFAULT '';
```

In `consensus/pricing.py` `refresh()`, extend the row build:

```python
            arch = m.get("architecture") or {}
            input_mods = arch.get("input_modalities") or ["text"]
            input_modalities = ",".join(input_mods)
            context_length = m.get("context_length")
            supported_parameters = ",".join(
                m.get("supported_parameters") or [])
            if model_id:
                rows.append((
                    model_id, prompt_cost, completion_cost, now,
                    input_modalities, context_length, supported_parameters,
                ))
```

and the INSERT:

```python
            self._conn.executemany(
                "INSERT OR REPLACE INTO model_pricing "
                "(model_id, prompt_cost, completion_cost, last_updated,"
                " input_modalities, context_length, supported_parameters) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
```

In `_lookup_row`, add `supported_parameters` to all four SELECT column lists (alias lookup, exact lookup, and the two LIKE loops), e.g.:

```python
                "SELECT model_id, prompt_cost, completion_cost, "
                "input_modalities, context_length, supported_parameters "
                "FROM model_pricing WHERE model_id = ?",
```

Add the method after `get_context_length`:

```python
    def supports_tools(self, model_name: str,
                       base_url: str = "") -> Optional[bool]:
        """Check if a model supports native tool/function calling.

        Uses the same fuzzy matching logic as ``lookup()`` and refreshes
        the cache once if the model is unknown.  Returns True/False when
        OpenRouter reports the model's supported parameters, or None
        when the model — or its capability data — is unknown (local
        models, rows cached before this column existed).
        """
        row = self._lookup_row(model_name, base_url)
        if row is None and self.needs_refresh_for_model(model_name):
            self.refresh()
            row = self._lookup_row(model_name, base_url)
        if row is None:
            return None
        params = (row.get("supported_parameters") or "").strip()
        if not params:
            return None
        return "tools" in params.split(",")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pricing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/migrations/014_supported_parameters.sql consensus/pricing.py tests/test_pricing.py
git commit -m "feat(pricing): cache supported_parameters and expose supports_tools() (#23)"
```

---

### Task 6: setup-time capability check in `start_discussion`

**Files:**
- Modify: `consensus/app_discussion_setup.py` (new helper + call in `start_discussion` after the Court of Law validation, ~line 346 — before any DB writes)
- Test: `tests/test_structured_setup_check.py` (new)

**Interfaces:**
- Consumes: `method.requires_structured_output()` (Task 2), `db.pricing.supports_tools()` (Task 5).
- Produces: `start_discussion` returns `{"error": "..."}` naming the entity, model, and method when a structured method is started with a model known to lack tool support. Unknown capability (`None`) does not block — a misconfigured local model fails loudly at runtime via `StructuredOutputError` instead.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_structured_setup_check.py`:

```python
"""Setup-time tool-capability check for structured methods (issue #23).

Owner decision (2026-07-12): structured methods may require
tool-capable models; the failure must be a clear setup-time error,
never a silent degrade.  Unknown capability (local models) is allowed
through — the runtime path raises loudly instead.
"""

import time

import pytest

from consensus.app_discussion_setup import start_discussion
from consensus.moderator import Moderator


def _insert_model(tmp_db, model_id: str, supported: str) -> None:
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    tmp_db.conn.commit()


def _start(disc, tmp_db):
    return start_discussion(disc, tmp_db, Moderator(disc, tmp_db))


class TestStructuredMethodSetupCheck:
    def test_blocks_model_without_tool_support(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)

        assert "error" in result
        assert "test-model" in result["error"]
        assert "tool" in result["error"].lower()

    def test_allows_tool_capable_model(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,tools")
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)
        assert result.get("started") is True

    def test_allows_unknown_capability(
            self, tmp_db, discussion_with_entities, monkeypatch):
        # No pricing row at all — e.g. a local model
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)
        assert result.get("started") is True

    def test_unstructured_method_never_blocks(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "open_discussion"

        result = _start(disc, tmp_db)
        assert result.get("started") is True
```

Note: these tests only fail once **Task 7** flags a Delphi handler with `requires_structured_output = True`. To keep this task independently red/green, ALSO add this direct unit test of the helper:

```python
def test_validate_helper_names_entity_and_method(
        tmp_db, discussion_with_entities, monkeypatch):
    import consensus.methods as methods_registry
    from consensus.app_discussion_setup import (
        _validate_structured_output_support,
    )
    from consensus.methods.base import DiscussionMethod, Phase
    from consensus.methods.phase_handler import PhaseHandler

    class _H(PhaseHandler):
        phase = Phase("p", "P")
        requires_structured_output = True

        def get_system_prompt(self, entity, discussion):
            return ""

        def get_turn_prompt(self, entity, discussion):
            return ""

    class _M(DiscussionMethod):
        name = "_test_setup_check"
        display_name = "Setup Check Test"
        description = "test"
        phase_handlers = (_H(),)

    monkeypatch.setitem(methods_registry._METHODS,
                        "_test_setup_check", _M)
    monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
    _insert_model(tmp_db, "test/test-model", "temperature")
    disc = discussion_with_entities
    disc.discussion_method = "_test_setup_check"

    error = _validate_structured_output_support(disc, tmp_db)
    assert "Alice" in error and "test-model" in error
```

and mark the three `delphi`-based tests with
`@pytest.mark.skip(reason="enabled by the Delphi conversion task")` — **remove the skips in Task 7 Step 4**.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_setup_check.py -q`
Expected: FAIL — `ImportError: cannot import name '_validate_structured_output_support'`.

- [ ] **Step 3: Implement**

In `consensus/app_discussion_setup.py`, add the helper (module level, above `start_discussion`; `EntityType`, `get_method`, and `Database` are already imported in this module — verify and add if missing):

```python
def _validate_structured_output_support(
    discussion: Discussion, db: Database,
) -> str:
    """Reject structured methods when a model is known to lack tool support.

    Structured methods force output tools at the API layer (issue #23);
    per the owner decision (2026-07-12) this must fail with a clear
    setup-time error rather than silently degrading.  All AI members are
    checked (including the moderator — some structured phases are
    moderator-only, e.g. Belief Diffusion framing).  Unknown capability
    (no OpenRouter data, e.g. local models) is allowed through: the
    runtime path raises a loud StructuredOutputError if the provider
    then rejects the forced call.

    Returns "" when the discussion may start, else the error message.
    """
    try:
        method = get_method(discussion.discussion_method)
    except KeyError:
        return ""  # open_discussion — no structured phases
    if not method.requires_structured_output():
        return ""
    for e in discussion.entities:
        if e.entity_type != EntityType.AI or not e.ai_config:
            continue
        supported = db.pricing.supports_tools(
            e.ai_config.model, e.ai_config.base_url)
        if supported is False:
            return (
                f"The {method.display_name} method requires structured "
                f"outputs via native tool calling, but {e.name}'s model "
                f"'{e.ai_config.model}' does not support tool calls. "
                f"Assign a tool-capable model to {e.name} or choose a "
                "different method."
            )
    return ""
```

In `start_discussion`, right after the Court of Law validation block (before `# Clear any stale state`):

```python
    # Structured methods need tool-capable models (issue #23)
    tool_error = _validate_structured_output_support(discussion, db)
    if tool_error:
        return {"error": tool_error}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_structured_setup_check.py -q`
Expected: PASS (3 skipped until Task 7).

- [ ] **Step 5: Commit**

```bash
git add consensus/app_discussion_setup.py tests/test_structured_setup_check.py
git commit -m "feat(setup): reject structured methods on non-tool-capable models (#23)"
```

---

### Task 7: convert Delphi estimate + revision phases

**Files:**
- Modify: `consensus/methods/phases/_delphi_helpers.py` (shared schema/validators/recorders)
- Modify: `consensus/methods/phases/estimate.py`
- Modify: `consensus/methods/phases/revise_delphi.py`
- Test: `tests/test_delphi_structured.py` (new); un-skip 3 tests in `tests/test_structured_setup_check.py`

**Interfaces:**
- Consumes: `OutputToolSpec` hooks (Task 2).
- Produces (in `_delphi_helpers.py`): `CONFIDENCE_LEVELS: tuple[str, ...]`; `ESTIMATE_TOOL_PARAMETERS: dict`; `validate_estimate_payload(payload: dict) -> str`; `record_estimate(state: dict, entity, round_num: int, value: float, confidence: str, unit: str) -> None`; `format_estimate_bar(value, confidence, unit) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_delphi_structured.py`:

```python
"""Structured-output conversion of the Delphi estimate/revise phases (#23)."""

from consensus.methods.phases._delphi_helpers import (
    validate_estimate_payload,
)
from consensus.methods.phases.estimate import EstimateHandler
from consensus.methods.phases.revise_delphi import ReviseDelphiHandler
from consensus.models import Discussion, Entity, EntityType


def _entity() -> Entity:
    return Entity(id=7, name="Alice", entity_type=EntityType.AI)


def _discussion(phase: str, **state) -> Discussion:
    disc = Discussion(topic="t", discussion_method="delphi")
    disc.method_state = {"current_phase": phase, "phase_round": 1,
                         "estimates": [], **state}
    return disc


PAYLOAD = {"estimate": 0.8, "confidence": "high", "unit": "probability",
           "reasoning": "Because of X, Y and Z."}


class TestValidateEstimatePayload:
    def test_valid(self):
        assert validate_estimate_payload(PAYLOAD) == ""

    def test_non_numeric_estimate(self):
        bad = dict(PAYLOAD, estimate="lots")
        assert "estimate" in validate_estimate_payload(bad)

    def test_bad_confidence(self):
        bad = dict(PAYLOAD, confidence="SURE")
        assert "confidence" in validate_estimate_payload(bad)

    def test_empty_reasoning(self):
        bad = dict(PAYLOAD, reasoning="  ")
        assert "reasoning" in validate_estimate_payload(bad)


class TestEstimateHandlerStructured:
    def test_declares_output_tool(self):
        handler = EstimateHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion("estimate"))
        assert spec.name == "submit_estimate"
        assert "estimate" in spec.parameters["properties"]

    def test_process_structured_records_round_zero(self):
        handler = EstimateHandler()
        disc = _discussion("estimate")
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["round"] == 0
        assert entry["value"] == 0.8
        assert entry["confidence"] == "HIGH"
        assert "Because of X" in processed.display_content
        assert "**Estimate:** 0.8" in processed.display_content

    def test_free_text_path_still_works_for_humans(self):
        handler = EstimateHandler()
        disc = _discussion("estimate")
        content = ('```json\n{"estimate": 0.5, "confidence": "LOW", '
                   '"unit": "probability"}\n```\nReasoning here.')
        handler.process_response(content, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["value"] == 0.5


class TestReviseHandlerStructured:
    def test_records_current_revision_round(self):
        handler = ReviseDelphiHandler()
        disc = _discussion("revise", revise_round=1)
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        [entry] = disc.method_state["estimates"]
        assert entry["round"] == 2

    def test_validate_delegates_to_shared_helper(self):
        handler = ReviseDelphiHandler()
        disc = _discussion("revise")
        assert handler.validate_output(PAYLOAD, _entity(), disc) == ""
        assert handler.validate_output({}, _entity(), disc) != ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_delphi_structured.py -q`
Expected: FAIL — `ImportError: cannot import name 'validate_estimate_payload'`.

- [ ] **Step 3: Implement**

Add to `consensus/methods/phases/_delphi_helpers.py`:

```python
#: Accepted confidence labels for estimate submissions.
CONFIDENCE_LEVELS: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW")

#: JSON Schema for the submit_estimate output tool (issue #23).
ESTIMATE_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "estimate": {
            "type": "number",
            "description": ("Your numeric estimate; use a probability "
                            "0.0-1.0 for non-numeric questions."),
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "unit": {
            "type": "string",
            "description": ("What the number represents, e.g. "
                            "'probability', 'USD', 'years'."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your detailed reasoning: supporting "
                            "evidence, key uncertainties, and what "
                            "would revise your estimate."),
        },
    },
    "required": ["estimate", "confidence", "unit", "reasoning"],
}


def validate_estimate_payload(payload: dict) -> str:
    """Return '' if a submit_estimate payload is usable, else an error."""
    try:
        float(payload.get("estimate"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ("'estimate' must be a number (use a probability 0.0-1.0 "
                "for non-numeric questions).")
    if str(payload.get("confidence", "")).upper() not in CONFIDENCE_LEVELS:
        return "'confidence' must be HIGH, MEDIUM, or LOW."
    if not str(payload.get("reasoning", "")).strip():
        return "'reasoning' must contain your detailed reasoning."
    return ""


def record_estimate(state: dict, entity: "Entity", round_num: int,
                    value: float, confidence: str, unit: str) -> None:
    """Append an estimate entry to method_state['estimates']."""
    state.setdefault("estimates", []).append({
        "round": round_num,
        "entity_id": entity.id,
        "entity_name": entity.name,
        "value": value,
        "confidence": confidence,
        "unit": unit,
    })


def format_estimate_bar(value: object, confidence: str, unit: str) -> str:
    """Return the display footer appended to estimate messages."""
    return f"\n\n---\n**Estimate:** {value} {unit} (Confidence: {confidence})"
```

(Use the module's existing `TYPE_CHECKING` import of `Entity`; add one if absent.)

In `consensus/methods/phases/estimate.py`:

1. Extend the helper import with `ESTIMATE_TOOL_PARAMETERS, format_estimate_bar, record_estimate, validate_estimate_payload`, and import `OutputToolSpec` from `..base`.
2. Add to `EstimateHandler`:

```python
    requires_structured_output = True

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_estimate",
            description=("Submit your independent estimate with "
                         "detailed reasoning."),
            parameters=ESTIMATE_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_estimate_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        value = float(payload["estimate"])
        confidence = str(payload["confidence"]).upper()
        unit = str(payload.get("unit", ""))
        record_estimate(discussion.method_state, entity, 0,
                        value, confidence, unit)
        display = (str(payload["reasoning"]).strip()
                   + format_estimate_bar(value, confidence, unit))
        return ProcessedResponse(display_content=display)
```

3. Refactor the existing `process_response` body to use the shared helpers (behavior unchanged):

```python
        if estimate_data:
            record_estimate(
                state, entity, 0,
                estimate_data.get("estimate"),
                estimate_data.get("confidence", ""),
                estimate_data.get("unit", ""),
            )
            display = content + format_estimate_bar(
                estimate_data.get("estimate", "?"),
                estimate_data.get("confidence", "?"),
                estimate_data.get("unit", ""),
            )
```

4. In `get_system_prompt`, replace the "You MUST include a JSON block..." paragraph (the fenced example) with:

```python
            "Submit your estimate by calling the submit_estimate tool "
            "with your estimate, confidence (HIGH/MEDIUM/LOW), unit, "
            "and detailed reasoning.\n\n"
```

and in `get_turn_prompt` replace the CRITICAL JSON-block instruction with:

```python
        return (
            f"It is your turn, {entity.name}.  Provide your independent "
            "estimate by calling the submit_estimate tool.  Put your "
            "full detailed reasoning in the 'reasoning' field."
        )
```

Apply the same four changes to `ReviseDelphiHandler` in `revise_delphi.py`, with `round_num = discussion.method_state.get("revise_round", 0) + 1` instead of `0`, tool description `"Submit your revised estimate after reviewing the group distribution."`, and turn prompt asking for the revised estimate via the tool.

5. Remove the three `@pytest.mark.skip` markers in `tests/test_structured_setup_check.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_delphi_structured.py tests/test_structured_setup_check.py tests/ -q -k "delphi or structured"`
Then the full suite: `uv run pytest tests/ -q` — existing Delphi tests (`tests/test_*delphi*`, method tests asserting prompt text) may reference the removed JSON-block wording; update those assertions to the new tool wording where they fail.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_delphi_helpers.py consensus/methods/phases/estimate.py consensus/methods/phases/revise_delphi.py tests/test_delphi_structured.py tests/test_structured_setup_check.py
git commit -m "feat(delphi): forced submit_estimate tool for estimate/revise phases (#23)"
```

---

### Task 8: convert the Voting vote phase

**Files:**
- Modify: `consensus/methods/phases/_voting_helpers.py` (schema + `record_votes`)
- Modify: `consensus/methods/phases/vote.py`
- Test: `tests/test_voting_structured.py` (new)

**Interfaces:**
- Produces (in `_voting_helpers.py`): `VOTES_TOOL_PARAMETERS: dict`; `record_votes(state: dict, entity, votes: list[dict]) -> int` — the validation/dedup/append loop extracted verbatim from `VoteHandler.process_response`, returning the number of accepted votes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voting_structured.py`:

```python
"""Structured-output conversion of the voting phase (#23)."""

from consensus.methods.phases.vote import VoteHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 7) -> Entity:
    return Entity(id=eid, name="Alice", entity_type=EntityType.AI)


def _discussion() -> Discussion:
    disc = Discussion(topic="t", discussion_method="voting")
    disc.method_state = {
        "current_phase": "vote", "phase_round": 1,
        "motions": [{"id": 1, "text": "Motion one"},
                    {"id": 2, "text": "Motion two"}],
        "votes": [],
    }
    return disc


PAYLOAD = {"votes": [
    {"motion_id": 1, "vote": "for", "rationale": "Good idea."},
    {"motion_id": 2, "vote": "against", "rationale": "Too risky."},
]}


class TestVoteValidation:
    def test_valid(self):
        assert VoteHandler().validate_output(
            PAYLOAD, _entity(), _discussion()) == ""

    def test_empty_votes_rejected(self):
        err = VoteHandler().validate_output(
            {"votes": []}, _entity(), _discussion())
        assert "votes" in err

    def test_unknown_motion_rejected(self):
        bad = {"votes": [{"motion_id": 99, "vote": "for",
                          "rationale": "x"}]}
        err = VoteHandler().validate_output(bad, _entity(), _discussion())
        assert "99" in err

    def test_invalid_vote_value_rejected(self):
        bad = {"votes": [{"motion_id": 1, "vote": "maybe",
                          "rationale": "x"}]}
        err = VoteHandler().validate_output(bad, _entity(), _discussion())
        assert "abstain" in err


class TestVoteProcessing:
    def test_structured_votes_recorded(self):
        handler = VoteHandler()
        disc = _discussion()
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        votes = disc.method_state["votes"]
        assert len(votes) == 2
        assert votes[0]["vote"] == "for"
        assert votes[0]["rationale"] == "Good idea."
        assert "**Votes cast:** 2" in processed.display_content

    def test_double_vote_deduplicated(self):
        handler = VoteHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        handler.process_structured_response(PAYLOAD, _entity(), disc)
        assert len(disc.method_state["votes"]) == 2

    def test_free_text_path_still_records(self):
        handler = VoteHandler()
        disc = _discussion()
        content = ('```json\n{"vote": "for", "motion_id": 1}\n```\n'
                   "Because reasons.")
        handler.process_response(content, _entity(), disc)
        assert len(disc.method_state["votes"]) == 1

    def test_declares_output_tool(self):
        handler = VoteHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_votes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_voting_structured.py -q`
Expected: FAIL — `validate_output` returns `""` for bad payloads (base default) and `process_structured_response` dumps JSON instead of recording votes.

- [ ] **Step 3: Implement**

Add to `consensus/methods/phases/_voting_helpers.py`:

```python
#: JSON Schema for the submit_votes output tool (issue #23).
VOTES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "votes": {
            "type": "array",
            "description": "One entry per motion you are voting on.",
            "items": {
                "type": "object",
                "properties": {
                    "motion_id": {"type": "integer"},
                    "vote": {"type": "string",
                             "enum": ["for", "against", "abstain"]},
                    "rationale": {"type": "string"},
                },
                "required": ["motion_id", "vote", "rationale"],
            },
        },
    },
    "required": ["votes"],
}
```

and move the per-vote acceptance loop out of `VoteHandler.process_response` into:

```python
def record_votes(state: dict, entity: "Entity",
                 votes: list[dict]) -> int:
    """Validate, dedupe, and append votes to state; return count accepted.

    Shared by the free-text and structured-output paths (issue #23).
    """
    valid_motion_ids = {m["id"] for m in state.get("motions", [])}
    accepted = 0
    for vote_data in votes:
        ...  # the existing loop body from VoteHandler.process_response,
             # verbatim (vote validation, int coercion, unknown-motion
             # skip, double-vote dedup, append, accepted += 1)
    return accepted
```

(Move the loop *verbatim* — including its logger warnings — replacing `self`-free references; `logger` already exists in `_voting_helpers.py`, add one if absent.)

Rewrite `VoteHandler.process_response` to:

```python
    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        accepted = record_votes(state, entity, extract_votes(content))
        if accepted:
            content += f"\n\n---\n**Votes cast:** {accepted}"
        return ProcessedResponse(display_content=content)
```

Add to `VoteHandler` (import `OutputToolSpec` from `..base` and the new helpers):

```python
    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        state = discussion.method_state
        return OutputToolSpec(
            name="submit_votes",
            description=("Cast your vote (for/against/abstain) with a "
                         "rationale on every pending motion:\n"
                         + format_motions_for_voting(state)),
            parameters=VOTES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        votes = payload.get("votes")
        if not isinstance(votes, list) or not votes:
            return "'votes' must be a non-empty array, one entry per motion."
        valid_ids = {m["id"]
                     for m in discussion.method_state.get("motions", [])}
        for v in votes:
            if not isinstance(v, dict):
                return "Each entry in 'votes' must be an object."
            try:
                motion_id = int(v.get("motion_id"))
            except (TypeError, ValueError):
                return "Each vote needs an integer 'motion_id'."
            if motion_id not in valid_ids:
                return (f"Motion {motion_id} does not exist. Valid motion "
                        f"ids: {sorted(valid_ids)}.")
            if str(v.get("vote", "")).lower() not in VALID_VOTES:
                return "Each 'vote' must be 'for', 'against', or 'abstain'."
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        votes = [{"motion_id": int(v["motion_id"]),
                  "vote": str(v["vote"]).lower(),
                  "rationale": str(v.get("rationale", ""))}
                 for v in payload["votes"]]
        accepted = record_votes(state, entity, votes)
        lines = [f"**Motion {v['motion_id']} — {v['vote'].upper()}:** "
                 f"{v['rationale']}" for v in votes]
        display = ("\n\n".join(lines)
                   + f"\n\n---\n**Votes cast:** {accepted}")
        return ProcessedResponse(display_content=display)
```

Also update `get_system_prompt`/`get_turn_prompt` to instruct calling `submit_votes` (replace the fenced-JSON instruction paragraphs, keeping the motion listings).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_voting_structured.py -q && uv run pytest tests/ -q -k voting`
Update any existing voting tests that assert the removed JSON-block prompt wording.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_voting_helpers.py consensus/methods/phases/vote.py tests/test_voting_structured.py
git commit -m "feat(voting): forced submit_votes tool for the vote phase (#23)"
```

---

### Task 9: convert Belief Diffusion framing (closes the #30 failure mode at the source)

**Files:**
- Modify: `consensus/methods/phases/_belief_helpers.py` (schema + count constants)
- Modify: `consensus/methods/phases/frame_hypotheses.py`
- Test: `tests/test_belief_framing_structured.py` (new)

**Interfaces:**
- Produces (in `_belief_helpers.py`): `MIN_HYPOTHESES = 2`, `MAX_HYPOTHESES = 6`, `HYPOTHESES_TOOL_PARAMETERS: dict`.
- The abort machinery from #30 (`framing_attempts` / `MAX_FRAMING_ATTEMPTS` / `next_phase -> None`) stays untouched — it now only engages when the structured path exhausts its retries AND the free-text fallback also fails to parse.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_belief_framing_structured.py`:

```python
"""Structured-output conversion of Belief Diffusion framing (#23).

The forced submit_hypotheses tool makes the #30 failure mode
(unparseable framing -> degenerate belief phases) effectively
impossible for tool-capable models; the existing abort machinery
remains as the last-resort containment.
"""

from consensus.methods.phases.frame_hypotheses import (
    FrameHypothesesHandler, MAX_FRAMING_ATTEMPTS,
)
from consensus.models import Discussion, Entity, EntityType


def _entity() -> Entity:
    return Entity(id=1, name="Mod", entity_type=EntityType.AI)


def _discussion() -> Discussion:
    disc = Discussion(topic="t", discussion_method="belief_diffusion")
    handler = FrameHypothesesHandler()
    disc.method_state = {"current_phase": "frame", "phase_round": 1,
                         **handler.init_state(disc)}
    return disc


PAYLOAD = {
    "hypotheses": [
        "The effect is caused by mechanism A",
        "The effect is caused by mechanism B",
        "The effect is a measurement artifact",
    ],
    "rationale": "These partition the plausible answer space.",
}


class TestFramingValidation:
    def test_valid(self):
        handler = FrameHypothesesHandler()
        assert handler.validate_output(PAYLOAD, _entity(),
                                       _discussion()) == ""

    def test_too_few_hypotheses(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": ["Only one sufficiently long hypothesis"]}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""

    def test_short_junk_hypotheses_rejected(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": ["a", "b", "c"]}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""

    def test_non_list_rejected(self):
        handler = FrameHypothesesHandler()
        bad = {"hypotheses": "H1, H2, H3"}
        assert handler.validate_output(bad, _entity(), _discussion()) != ""


class TestFramingProcessing:
    def test_hypotheses_recorded_and_displayed(self):
        handler = FrameHypothesesHandler()
        disc = _discussion()
        processed = handler.process_structured_response(
            PAYLOAD, _entity(), disc)
        assert disc.method_state["hypotheses"] == PAYLOAD["hypotheses"]
        assert "1. The effect is caused by mechanism A" \
            in processed.display_content
        assert "partition the plausible answer space" \
            in processed.display_content
        # Framing succeeded -> phase advances
        assert handler.should_advance(disc) is True

    def test_declares_output_tool(self):
        handler = FrameHypothesesHandler()
        assert handler.requires_structured_output is True
        spec = handler.get_output_tool(_entity(), _discussion())
        assert spec.name == "submit_hypotheses"

    def test_abort_machinery_untouched(self):
        handler = FrameHypothesesHandler()
        disc = _discussion()
        disc.method_state["framing_attempts"] = MAX_FRAMING_ATTEMPTS
        assert handler.should_advance(disc) is True
        assert handler.next_phase(disc) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_belief_framing_structured.py -q`
Expected: FAIL — default `validate_output` returns `""` for bad payloads; `process_structured_response` JSON-dumps instead of recording.

- [ ] **Step 3: Implement**

Add to `consensus/methods/phases/_belief_helpers.py` (near `MIN_HYPOTHESIS_LENGTH`):

```python
#: Acceptable hypothesis-count bounds for structured framing (the
#: prompt asks for 3-5; validation is slightly tolerant).
MIN_HYPOTHESES = 2
MAX_HYPOTHESES = 6

#: JSON Schema for the submit_hypotheses output tool (issue #23).
HYPOTHESES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
            "description": ("3-5 competing, mutually exclusive "
                            "hypotheses that together cover the "
                            "plausible answer space."),
        },
        "rationale": {
            "type": "string",
            "description": ("Brief explanation of how the hypotheses "
                            "partition the answer space."),
        },
    },
    "required": ["hypotheses"],
}
```

In `consensus/methods/phases/frame_hypotheses.py`, import `OutputToolSpec` from `..base` and the new names (plus `MIN_HYPOTHESIS_LENGTH`) from `._belief_helpers`, then add to `FrameHypothesesHandler`:

```python
    requires_structured_output = True

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_hypotheses",
            description=("Submit the 3-5 competing hypotheses that "
                         "frame this Belief Diffusion exercise."),
            parameters=HYPOTHESES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        hypotheses = payload.get("hypotheses")
        if not isinstance(hypotheses, list):
            return "'hypotheses' must be an array of strings."
        cleaned = [str(h).strip() for h in hypotheses if str(h).strip()]
        if not (MIN_HYPOTHESES <= len(cleaned) <= MAX_HYPOTHESES):
            return (f"Provide between {MIN_HYPOTHESES} and "
                    f"{MAX_HYPOTHESES} hypotheses (aim for 3-5).")
        if any(len(h) <= MIN_HYPOTHESIS_LENGTH for h in cleaned):
            return ("Each hypothesis must be a specific, substantive "
                    "statement, not a label.")
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        hypotheses = [str(h).strip() for h in payload["hypotheses"]
                      if str(h).strip()]
        state["hypotheses"] = hypotheses
        logger.info("Recorded %d hypotheses from structured framing",
                    len(hypotheses))
        numbered = "\n".join(f"{i}. {h}"
                             for i, h in enumerate(hypotheses, 1))
        rationale = str(payload.get("rationale", "")).strip()
        display = (f"{rationale}\n\n{numbered}" if rationale else numbered)
        return ProcessedResponse(display_content=display)
```

Update `get_turn_prompt` — the first-attempt branch becomes:

```python
        return (
            "Decompose the topic into 3-5 competing hypotheses.  Each "
            "should be specific and mutually exclusive where possible.  "
            "Submit them by calling the submit_hypotheses tool."
        )
```

and the retry branch (`framing_attempts > 0`):

```python
            return (
                "The previous framing was not usable.  Please call the "
                "submit_hypotheses tool with 3-5 specific, mutually "
                "exclusive hypotheses, one complete statement each."
            )
```

Keep `process_response`, `should_advance`, `next_phase`, and `get_method_complete_message` exactly as they are — they are the free-text fallback and the #30 abort containment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_belief_framing_structured.py tests/test_belief_diffusion_abort.py -q`
Then the full suite: `uv run pytest tests/ -q` — fix any belief-diffusion prompt-wording assertions.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_belief_helpers.py consensus/methods/phases/frame_hypotheses.py tests/test_belief_framing_structured.py
git commit -m "feat(belief-diffusion): forced submit_hypotheses tool for framing (#23)"
```

---

### Task 10: documentation and handover

**Files:**
- Modify: `HANDOVER.md`
- Modify: `CLAUDE.md` (add `structured_output.py` to the Key modules list, one line)

**Interfaces:** none (documentation).

- [ ] **Step 1: Update `CLAUDE.md`**

Add under "Key modules" after the `context_strategies.py` entry:

```markdown
- `structured_output.py` — Forced tool-call turn generation for structured method phases (issue #23): phases declare an `OutputToolSpec`, the model must call it (`tool_choice`), payloads are validated by the handler's `validate_output` hook with bounded retries (`MAX_STRUCTURED_OUTPUT_ATTEMPTS`), and non-tool-capable models are rejected at discussion setup via `PricingCache.supports_tools()`
```

- [ ] **Step 2: Update `HANDOVER.md`**

- Move #23 from "Next steps" into "Where things stand" (mechanism + Delphi/voting/framing conversions done; name the key files and hooks).
- List the remaining regex-parsing phases as a follow-up conversion checklist: `prior_beliefs`/`diffuse_beliefs` (belief distributions), `blind_evaluate`/`tally`, `evaluate_matrix`/`define_criteria`, `distill_skeleton`, `hypothesize`, `surface_assumptions`, `decompose`, `counterfactual_extract` — each now a mechanical conversion following the Task 7-9 pattern.
- Note two known gaps as explicit follow-ups: (a) `switch_discussion_method` (triage handoff) does not run the tool-capability check, so triage can still switch into a structured method with a non-tool-capable model; (b) `MethodRecommender` does not consider tool capability when recommending methods.
- Keep the "Next steps" ordering for #24/#25/#27/#26 and cross-cutting items.

- [ ] **Step 3: Run the full suite one final time**

Run: `uv run pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add HANDOVER.md CLAUDE.md
git commit -m "docs: record structured-output mechanism and follow-ups (#23)"
```

---

## Self-Review Notes

- **Spec coverage:** forced tool calls at the API layer (Tasks 1, 3), phase-declared output tools (Task 2), validation-error retries instead of generic "try again" (Task 3), setup-time error for non-tool-capable models per owner decision (Tasks 5, 6), regex path demoted to human-input/containment fallback (Tasks 4, 7–9), named phases from the issue: estimates (Task 7), votes (Task 8), hypotheses (Task 9); ratings/skeletons documented as mechanical follow-ups (Task 10).
- **Type consistency:** `OutputToolSpec(name, description, parameters)` + `to_openai_schema()`; hooks `get_output_tool(entity, discussion)`, `validate_output(payload, entity, discussion) -> str`, `process_structured_response(payload, entity, discussion) -> ProcessedResponse` used identically across Tasks 2–4 and 7–9; `generate_structured_turn(client, cfg, entity, discussion, messages, spec, method)` consistent between Tasks 3 and 4; `supports_tools` returning `Optional[bool]` consistent between Tasks 5 and 6.
- **Known simplifications (intentional):** structured turns don't offer registry tools (web search etc.) — the phase output is the task; the setup check covers all AI members including the moderator (over-strict beats silent degrade); triage's runtime method switch is documented as a follow-up gap rather than silently half-fixed.
