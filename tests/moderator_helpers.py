"""Shared fakes for the ``Moderator`` characterization tests.

The only fake on the AI path is ``AIClient._post_with_retry`` — the HTTP
edge — so ``complete`` and ``complete_with_tools`` run their real parsing
(including DSML promotion). Import the fixtures by name into a test module.
"""

from __future__ import annotations

from typing import Union
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from consensus.ai_client import AIClient
from consensus.models import Discussion, Entity
from consensus.moderator import Moderator
from consensus.tools import ToolDefinition, ToolResult

#: Usage the fake provider reports for every completion.
PROMPT_TOKENS = 3
COMPLETION_TOKENS = 4

#: A scripted reply: an assistant message dict, or an HTTP error status.
Reply = Union[dict, int]


class ScriptedLLM:
    """Answers completion requests from a queue and records each payload.

    Each queued reply is an assistant ``message`` dict or an ``int`` HTTP
    status (answered as an error). An empty queue answers
    ``{"content": DEFAULT_REPLY}``.
    """

    DEFAULT_REPLY = "default reply"

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.payloads: list[dict] = []
        self.api_keys: list[str] = []

    def respond(self, client: AIClient, url: str, payload: dict) -> httpx.Response:
        """Record the request and build the provider's HTTP response."""
        # Copy: the moderator keeps appending to the same messages list.
        self.payloads.append({**payload, "messages": list(payload["messages"])})
        self.api_keys.append(client.api_key)
        request = httpx.Request("POST", url)
        reply = self.replies.pop(0) if self.replies else {
            "content": self.DEFAULT_REPLY}
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": "boom"}, request=request)
        body = {
            "model": payload["model"],
            "choices": [{"message": {"role": "assistant", **reply},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": PROMPT_TOKENS,
                      "completion_tokens": COMPLETION_TOKENS,
                      "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS},
        }
        return httpx.Response(200, json=body, request=request)


@pytest.fixture
def scripted_llm(monkeypatch) -> ScriptedLLM:
    """Replace the HTTP edge of ``AIClient`` with a ``ScriptedLLM``."""
    fake = ScriptedLLM()

    async def fake_post(self, url, payload, model):
        return fake.respond(self, url, payload)

    monkeypatch.setattr(AIClient, "_post_with_retry", fake_post)
    return fake


@pytest.fixture
def mod(tmp_db, sample_ai_entity, sample_human_entity):
    """A ``Moderator`` over a persisted discussion: AI moderator + human."""
    ai = Entity.from_db_row(tmp_db.get_entity(sample_ai_entity))
    human = Entity.from_db_row(tmp_db.get_entity(sample_human_entity))
    disc = Discussion(
        topic="AI ethics", entities=[ai, human], moderator_id=ai.id,
        turn_order=[ai.id, human.id], current_turn_index=0, turn_number=1,
        is_active=True, status="active",
    )
    disc.id = tmp_db.create_discussion(disc.topic, ai.id)
    for position, entity in enumerate((ai, human)):
        tmp_db.add_discussion_member(disc.id, entity.id, turn_position=position)
    return Moderator(disc, tmp_db), disc, ai, human


def tool_call(name: str, arguments: str = "{}", call_id: str = "tc1") -> dict:
    """An OpenAI-format tool call as a provider returns it."""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def dsml_call(name: str, **params: str) -> str:
    """DeepSeek DSML markup for one tool call, as some models emit it."""
    bar = "｜"
    body = "".join(
        f'<{bar}DSML{bar}parameter name="{k}">{v}</{bar}DSML{bar}parameter>'
        for k, v in params.items())
    return (f"<{bar}DSML{bar}function_calls>"
            f'<{bar}DSML{bar}invoke name="{name}">{body}'
            f"</{bar}DSML{bar}invoke></{bar}DSML{bar}function_calls>")


def install_registry(moderator: Moderator, *names: str,
                     result: ToolResult | None = None) -> MagicMock:
    """Give *moderator* a registry offering *names*; each call returns *result*."""
    registry = MagicMock()
    registry.get_tools_for_entity = AsyncMock(return_value=[
        ToolDefinition(name=n, description="", parameters={}) for n in names])
    registry.execute = AsyncMock(
        return_value=result or ToolResult(content="results"))
    moderator._tool_registry = registry
    return registry
