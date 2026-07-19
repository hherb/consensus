"""Tests for AIClient._post_with_retry temperature-drop handling."""

from __future__ import annotations

import httpx
import pytest

from consensus.ai_client import AIClient, MAX_RETRIES


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


class _FakeClient:
    """Stand-in httpx client returning a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.is_closed = False
        self.posts: list[dict] = []

    async def post(self, url, json):
        # Record the payload as seen at call time.
        self.posts.append(dict(json))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_temperature_drop_on_final_attempt_still_retries(monkeypatch):
    # The temperature rejection arrives on what would be the last attempt;
    # dropping temperature must not exhaust the budget and return the 400.
    client = AIClient(base_url="https://api.example.com")
    rejection = _FakeResponse(400, "Invalid temperature value")
    success = _FakeResponse(200, "ok")
    # MAX_RETRIES retryable-equivalent slots all consumed, then the rejection,
    # then success — proves the drop did not count against the budget.
    fake = _FakeClient([rejection, success])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {"model": "m", "temperature": 0.7, "messages": []}
    resp = await client._post_with_retry("/chat", payload, "m")

    assert resp.status_code == 200
    # Temperature was present on the first call and dropped on the retry.
    assert "temperature" in fake.posts[0]
    assert "temperature" not in fake.posts[1]


@pytest.mark.asyncio
async def test_deprecated_temperature_dropped(monkeypatch):
    # Anthropic's OpenAI-compat endpoint rejects temperature for newer
    # models with "deprecated" wording rather than "invalid temperature";
    # any 400 naming a droppable parameter must trigger the drop.
    client = AIClient(base_url="https://api.example.com")
    rejection = _FakeResponse(
        400, '{"error":{"message":"`temperature` is deprecated for this '
             'model.","type":"invalid_request_error"}}')
    success = _FakeResponse(200, "ok")
    fake = _FakeClient([rejection, success])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {"model": "m", "temperature": 0.7, "messages": []}
    resp = await client._post_with_retry("/chat", payload, "m")

    assert resp.status_code == 200
    assert "temperature" not in fake.posts[1]


@pytest.mark.asyncio
async def test_unmentioned_param_not_dropped(monkeypatch):
    # A 400 naming one parameter must not drop others the payload carries.
    client = AIClient(base_url="https://api.example.com")
    rejection = _FakeResponse(400, "top_p is not supported")
    success = _FakeResponse(200, "ok")
    fake = _FakeClient([rejection, success])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {"model": "m", "temperature": 0.7, "top_p": 0.9,
               "messages": []}
    resp = await client._post_with_retry("/chat", payload, "m")

    assert resp.status_code == 200
    assert "top_p" not in fake.posts[1]
    assert fake.posts[1]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_non_temperature_400_is_returned(monkeypatch):
    client = AIClient(base_url="https://api.example.com")
    fake = _FakeClient([_FakeResponse(400, "bad request")])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {"model": "m", "temperature": 0.7, "messages": []}
    resp = await client._post_with_retry("/chat", payload, "m")
    assert resp.status_code == 400
    # Only one call — a non-temperature 400 is not retried.
    assert len(fake.posts) == 1


@pytest.mark.asyncio
async def test_forced_tool_choice_downgraded_to_required(monkeypatch):
    # Moonshot thinking models (kimi-k3, kimi-k2.5) reject a tool_choice
    # naming a specific function but accept "required"; the client must
    # downgrade and retry instead of surfacing the 400.
    client = AIClient(base_url="https://api.example.com")
    rejection = _FakeResponse(
        400, '{"error":{"message":"tool_choice \'specified\' is '
             'incompatible with thinking enabled"}}')
    success = _FakeResponse(200, "ok")
    fake = _FakeClient([rejection, success])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {
        "model": "m", "messages": [],
        "tools": [{"type": "function", "function": {"name": "submit_x"}}],
        "tool_choice": {"type": "function", "function": {"name": "submit_x"}},
    }
    resp = await client._post_with_retry("/chat", payload, "m")

    assert resp.status_code == 200
    assert fake.posts[0]["tool_choice"] == {
        "type": "function", "function": {"name": "submit_x"}}
    assert fake.posts[1]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_tool_choice_downgrade_is_one_shot(monkeypatch):
    # If the provider rejects "required" as well, the 400 is returned
    # rather than looping forever.
    client = AIClient(base_url="https://api.example.com")
    first = _FakeResponse(400, "tool_choice 'specified' not supported")
    second = _FakeResponse(400, "tool_choice 'required' not supported")
    fake = _FakeClient([first, second])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {
        "model": "m", "messages": [],
        "tool_choice": {"type": "function", "function": {"name": "submit_x"}},
    }
    resp = await client._post_with_retry("/chat", payload, "m")
    assert resp.status_code == 400
    assert len(fake.posts) == 2


@pytest.mark.asyncio
async def test_unrelated_400_keeps_forced_tool_choice(monkeypatch):
    # A 400 that does not mention tool_choice must not trigger the
    # downgrade — it is returned for the caller's error handling.
    client = AIClient(base_url="https://api.example.com")
    fake = _FakeClient([_FakeResponse(400, "context length exceeded")])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {
        "model": "m", "messages": [],
        "tool_choice": {"type": "function", "function": {"name": "submit_x"}},
    }
    resp = await client._post_with_retry("/chat", payload, "m")
    assert resp.status_code == 400
    assert len(fake.posts) == 1


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
