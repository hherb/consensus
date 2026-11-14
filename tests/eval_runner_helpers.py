"""Shared fake LLM edge for the evaluation-runner tests.

The runner drives a real ``ConsensusApp``; the only fake is
``AIClient._post_with_retry`` — the HTTP edge — so ``complete`` and
``complete_with_tools`` run their real parsing code. Import ``llm`` by name
into a test module to use it.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx
import pytest

from consensus.ai_client import AIClient

#: Tokens the fake provider reports per completion.
TOKENS_PER_CALL = 7


class FakeLLM:
    """Records every completion request and answers it.

    ``fail_when(payload) -> bool`` makes matching requests return HTTP 500;
    ``yield_each_call`` yields to the event loop first, so concurrent runs
    interleave.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_when: Callable[[dict], bool] = lambda payload: False
        self.yield_each_call = False

    def respond(self, client: AIClient, url: str, payload: dict) -> httpx.Response:
        """Record the request and build the provider's HTTP response."""
        self.calls.append({
            "base_url": client.base_url, "api_key": client.api_key,
            "model": payload["model"], "messages": payload["messages"],
            "tools": payload.get("tools"),
        })
        request = httpx.Request("POST", url)
        if self.fail_when(payload):
            return httpx.Response(500, json={"error": "boom"}, request=request)
        body = {
            "model": payload["model"],
            "choices": [{"message": {"role": "assistant",
                                     "content": f"Reply {len(self.calls)}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4,
                      "total_tokens": TOKENS_PER_CALL},
        }
        return httpx.Response(200, json=body, request=request)


@pytest.fixture
def llm(monkeypatch) -> FakeLLM:
    """Replace the HTTP edge of ``AIClient`` with a ``FakeLLM``."""
    fake = FakeLLM()

    async def fake_post(self, url, payload, model):
        if fake.yield_each_call:
            await asyncio.sleep(0)
        return fake.respond(self, url, payload)

    monkeypatch.setattr(AIClient, "_post_with_retry", fake_post)
    return fake


def _last(payload: dict) -> str:
    return str(payload["messages"][-1].get("content", ""))


def is_turn(payload: dict) -> bool:
    """True for a participant's turn request."""
    return _last(payload).startswith("It is your turn to speak")


def is_summary(payload: dict) -> bool:
    """True for the moderator's per-turn summary request."""
    return "has just completed" in _last(payload)


def is_synthesis(payload: dict) -> bool:
    """True for the moderator's final-synthesis request."""
    return not is_turn(payload) and not is_summary(payload)
