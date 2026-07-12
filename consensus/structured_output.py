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
