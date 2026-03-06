"""OpenAI-compatible API client using httpx."""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

# Default timeout for API requests (seconds)
DEFAULT_API_TIMEOUT = 120.0


@dataclass
class AIResponse:
    """Response from an AI completion, including usage metadata."""
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    tool_calls: list = field(default_factory=list)  # list[ToolCallRecord]


class AIClient:
    """Async client for OpenAI-compatible chat completion APIs.

    Reuses an httpx.AsyncClient for connection pooling. Callers should
    call ``close()`` when done, or use the client as an async context
    manager.
    """

    def __init__(self, base_url: str, api_key: str = "",
                 timeout: float = DEFAULT_API_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared AsyncClient, creating it lazily."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout, headers=headers,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AIClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _is_anthropic(self) -> bool:
        return "api.anthropic.com" in self.base_url

    async def list_models(self) -> list[str]:
        """Fetch available model IDs from the provider's models endpoint."""
        try:
            if self._is_anthropic():
                return await self._list_models_anthropic()
            client = self._get_client()
            response = await client.get(f"{self.base_url}/models")
            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])
            return sorted(m["id"] for m in models if "id" in m)
        except Exception:
            logger.debug("Failed to list models from %s", self.base_url,
                         exc_info=True)
            return []

    async def _list_models_anthropic(self) -> list[str]:
        """Fetch models from Anthropic's API (uses x-api-key auth)."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        async with httpx.AsyncClient(timeout=self.timeout,
                                     headers=headers) as client:
            models: list[str] = []
            url = f"{self.base_url}/models?limit=100"
            while url:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                for m in data.get("data", []):
                    if "id" in m:
                        models.append(m["id"])
                if data.get("has_more"):
                    last = data.get("last_id", "")
                    url = f"{self.base_url}/models?limit=100&after_id={last}"
                else:
                    url = ""
            return sorted(models)

    async def complete(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AIResponse:
        """Send a chat completion request and return response with metadata."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        client = self._get_client()
        start = time.monotonic()
        response = await client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = int((time.monotonic() - start) * 1000)

        usage = data.get("usage", {})
        return AIResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=elapsed,
        )

    async def complete_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict:
        """Send a chat completion with tools and return the full message dict.

        Unlike complete(), this returns the raw choices[0].message dict
        which may contain tool_calls in addition to content.
        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        client = self._get_client()
        start = time.monotonic()
        response = await client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = int((time.monotonic() - start) * 1000)

        choice = data["choices"][0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return {
            "message": message,
            "finish_reason": choice.get("finish_reason", "stop"),
            "model": data.get("model", model),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": elapsed,
        }

    async def stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Stream a chat completion response, yielding content chunks."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        client = self._get_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    content = chunk["choices"][0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    logger.debug("Failed to parse SSE chunk: %s", data)
                    continue
