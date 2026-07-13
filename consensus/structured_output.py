"""Forced tool-call turn generation for structured method phases.

When a phase declares an ``OutputToolSpec``, AI turns in that phase are
generated here instead of through free-text completion: the model is
forced (via ``tool_choice``) to call the declared tool, the parsed
arguments are validated by the method's ``validate_output`` hook, and
validation errors are fed back for a bounded number of retries
(issue #23).

Failure containment: if the provider rejects the request with an
HTTP 400 that mentions tools/functions (a model without tool support)
a ``StructuredOutputError`` is raised so the misconfiguration is loud,
never a silent degrade (owner decision 2026-07-12).  Unrelated provider
errors propagate unchanged to the normal per-turn error handling.  If a
tool-capable model keeps producing invalid payloads, the turn falls
back to the free-text path with a user-visible warning — the phase's
own give-up caps (e.g. ``MAX_FRAMING_ATTEMPTS``) then bound the damage.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

import httpx

from .ai_client import AIClient, AIResponse
from .database import Database
from .methods import get_method
from .models import EntityType

if TYPE_CHECKING:
    from .methods.base import DiscussionMethod, OutputToolSpec
    from .models import AIConfig, Discussion, Entity

logger = logging.getLogger(__name__)

#: Bounded retries for invalid structured outputs before falling back
#: to the free-text parsing path.
MAX_STRUCTURED_OUTPUT_ATTEMPTS = 3

#: Maximum length of the provider error body echoed into a
#: StructuredOutputError message.
_PROVIDER_ERROR_SNIPPET_LENGTH = 200

#: Substrings that identify a provider 400 as a tool-support rejection
#: (as opposed to e.g. a context-length overflow).
_TOOL_ERROR_MARKERS = ("tool", "function")


class StructuredOutputError(RuntimeError):
    """The provider/model cannot satisfy a phase's forced output tool."""


def _validate_structured_output_support(
    discussion: "Discussion", db: Database, method_name: Optional[str] = None,
) -> str:
    """Reject structured methods when a model is known to lack tool support.

    Structured methods force output tools at the API layer (issue #23);
    per the owner decision (2026-07-12) this must fail with a clear
    error rather than silently degrading.  All AI members are checked
    (including the moderator — some structured phases are moderator-only,
    e.g. Belief Diffusion framing).  Unknown capability (no OpenRouter
    data, e.g. local models) is allowed through: the runtime path raises
    a loud ``StructuredOutputError`` if the provider then rejects the
    forced call.

    Lives here (rather than in ``app_discussion_setup``, its original
    home) because both the setup-time gate (``start_discussion``) and
    the runtime gate (``switch_discussion_method`` in
    ``app_discussion_flow``, used by Triage's handoff) need it, and
    ``app_discussion_setup``/``app_discussion_flow`` import each other —
    importing one from the other here would create a circular import.
    ``app_discussion_setup`` re-exports this name for backward
    compatibility.

    Args:
        discussion: The discussion whose entities are checked.
        db: Database handle providing the pricing cache.
        method_name: The method to validate against. Defaults to
            ``discussion.discussion_method`` (the setup-time case,
            where the target method has already been assigned).
            Callers that validate a *prospective* switch — where
            ``discussion.discussion_method`` still holds the old
            method — must pass the target explicitly.

    Returns "" when the discussion may proceed, else the error message.
    """
    target = (
        method_name if method_name is not None else discussion.discussion_method
    )
    try:
        method = get_method(target)
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


def _is_tool_support_error(body: str) -> bool:
    """Heuristically classify a provider 400 body as a tool-support error.

    Providers return 400 for many reasons (context overflow, malformed
    requests, content filters); only bodies that mention tools or
    functions should be reported as "model lacks tool support".
    """
    lowered = body.lower()
    return any(marker in lowered for marker in _TOOL_ERROR_MARKERS)


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
            call with a tool-related HTTP 400, i.e. the model/provider
            lacks tool support.  Other HTTP errors propagate unchanged.
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
            body = exc.response.text or ""
            if (exc.response.status_code == 400
                    and _is_tool_support_error(body)):
                snippet = body[:_PROVIDER_ERROR_SNIPPET_LENGTH].strip()
                raise StructuredOutputError(
                    f"{cfg.model} rejected the forced tool call required "
                    f"by this phase ({spec.name}). Assign a tool-capable "
                    f"model to {entity.name} or choose a method without "
                    f"structured phases. Provider error: {snippet}"
                ) from exc
            # Other errors (context overflow, provider hiccups, ...) are
            # not a tool-capability problem — let the normal per-turn
            # error handling report them.  Log the body here: it is the
            # only diagnostic str(HTTPStatusError) does not carry.
            logger.warning(
                "Provider error %d for %s during structured turn: %s",
                exc.response.status_code, cfg.model,
                body[:_PROVIDER_ERROR_SNIPPET_LENGTH],
            )
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
