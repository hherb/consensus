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
async def test_non_temperature_400_is_returned(monkeypatch):
    client = AIClient(base_url="https://api.example.com")
    fake = _FakeClient([_FakeResponse(400, "bad request")])
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    payload = {"model": "m", "temperature": 0.7, "messages": []}
    resp = await client._post_with_retry("/chat", payload, "m")
    assert resp.status_code == 400
    # Only one call — a non-temperature 400 is not retried.
    assert len(fake.posts) == 1
