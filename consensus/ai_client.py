"""OpenAI-compatible API client using httpx."""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

# Default timeout for API requests (seconds)
DEFAULT_API_TIMEOUT = 120.0

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds — doubles each retry
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Optional sampling parameters some models reject outright — e.g. Kimi
# K2.5's "invalid temperature: only 1 is allowed" or Anthropic's
# "`temperature` is deprecated for this model".  When a 400 names one
# that the payload carries, it is dropped and the request retried with
# the provider's default.  max_tokens is deliberately absent: it bounds
# cost, and providers that require a different spelling get it from
# _max_tokens_param instead.
DROPPABLE_PARAMS = ("temperature", "top_p", "frequency_penalty",
                    "presence_penalty")

# Regex patterns for DeepSeek DSML tool-call markup that some models
# (notably deepseek-reasoner) emit as plain text instead of structured
# tool_calls in the JSON response.
_DSML_INVOKE_RE = re.compile(
    r'<\uff5cDSML\uff5cinvoke\s+name="([^"]+)"\s*>(.*?)</\uff5cDSML\uff5cinvoke>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r'<\uff5cDSML\uff5cparameter\s+name="([^"]+)"(?:\s+string="[^"]*")?\s*>'
    r'(.*?)'
    r'</\uff5cDSML\uff5cparameter>',
    re.DOTALL,
)
_DSML_BLOCK_RE = re.compile(
    r'<\uff5cDSML\uff5cfunction_calls>.*?</\uff5cDSML\uff5cfunction_calls>',
    re.DOTALL,
)


def _parse_dsml_tool_calls(content: str) -> tuple[list[dict], str]:
    """Extract DSML-formatted tool calls from content text.

    Returns (tool_calls, remaining_content) where tool_calls is in OpenAI
    format and remaining_content is the text with DSML blocks removed.
    """
    if '\uff5cDSML\uff5c' not in content:
        return [], content

    tool_calls = []
    for match in _DSML_INVOKE_RE.finditer(content):
        func_name = match.group(1)
        body = match.group(2)
        arguments = {}
        for param_match in _DSML_PARAM_RE.finditer(body):
            param_name = param_match.group(1)
            param_value = param_match.group(2).strip()
            # Try to parse as JSON value (numbers, booleans, etc.)
            try:
                arguments[param_name] = json.loads(param_value)
            except (json.JSONDecodeError, ValueError):
                arguments[param_name] = param_value

        tool_calls.append({
            "id": f"dsml_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": func_name,
                "arguments": json.dumps(arguments),
            },
        })

    if tool_calls:
        logger.info(
            "Parsed %d DSML tool call(s) from content: %s",
            len(tool_calls),
            [tc["function"]["name"] for tc in tool_calls],  # type: ignore[index]
        )

    # Remove DSML blocks from content
    remaining = _DSML_BLOCK_RE.sub('', content).strip()
    return tool_calls, remaining


def _normalize_content(raw: object) -> str:
    """Ensure content is a plain string.

    Some APIs return content as a list of content blocks
    (e.g. [{"type": "text", "text": "..."}]) instead of a string.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if raw is None:
        return ""
    return str(raw)


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
    warning: str = ""
    structured_output: Optional[dict] = None  # forced-tool-call payload (issue #23)
    #: Provider finish reason ("stop", "length", ...) — "length" means the
    #: completion budget ran out, which for reasoning models can leave the
    #: visible content empty.
    finish_reason: str = ""


class AIClient:
    """Async client for OpenAI-compatible chat completion APIs.

    Reuses an httpx.AsyncClient for connection pooling. Callers should
    call ``close()`` when done, or use the client as an async context
    manager.
    """

    # Hosts that require max_completion_tokens instead of max_tokens
    _MAX_COMPLETION_TOKENS_HOSTS = ("api.openai.com",)

    def __init__(self, base_url: str, api_key: str = "",
                 timeout: float = DEFAULT_API_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _max_tokens_param(self, max_tokens: int) -> dict:
        """Return the appropriate max-tokens payload key for this API."""
        from urllib.parse import urlparse
        host = urlparse(self.base_url).hostname or ""
        if any(h in host for h in self._MAX_COMPLETION_TOKENS_HOSTS):
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens}

    async def _post_with_retry(
        self, url: str, payload: dict, model: str,
    ) -> httpx.Response:
        """POST with exponential backoff for retryable errors."""
        client = self._get_client()
        last_exc: Optional[Exception] = None

        # Manual attempt counter so a one-shot temperature drop does not
        # consume a retry slot (which would otherwise return the 400 when the
        # rejection happens on the final attempt).
        attempt = 0
        while True:
            try:
                response = await client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Request to %s failed (attempt %d/%d): %s. "
                        "Retrying in %.1fs...",
                        model, attempt + 1, MAX_RETRIES, exc, delay,
                    )
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise

            # Some models reject optional sampling parameters outright
            # (see DROPPABLE_PARAMS).  Drop every parameter the error
            # body names from the payload and retry with the provider's
            # defaults.  This is a one-shot fix per parameter (the
            # payload no longer carries it afterwards) so it does not
            # increment ``attempt`` and cannot exhaust the retry budget.
            if response.status_code == 400:
                body = response.text.lower()
                rejected = [p for p in DROPPABLE_PARAMS
                            if p in payload and p in body]
                if rejected:
                    logger.info(
                        "Model %s rejected parameter(s) %s; retrying "
                        "with provider defaults",
                        model, ", ".join(rejected),
                    )
                    for param in rejected:
                        payload.pop(param, None)
                    continue

            # Some models reject a tool_choice naming a specific function
            # while accepting the broader "required" mode (e.g. Moonshot
            # thinking models: "tool_choice 'specified' is incompatible
            # with thinking enabled").  Callers forcing a tool only ever
            # offer that single tool, so "required" is an equivalent
            # forcing.  One-shot for the same reason as the temperature
            # drop above: tool_choice is no longer a dict afterwards.
            if (response.status_code == 400
                    and isinstance(payload.get("tool_choice"), dict)
                    and "tool_choice" in response.text.lower()):
                logger.info(
                    "Model %s rejected forced tool_choice; "
                    "retrying with 'required'", model,
                )
                payload["tool_choice"] = "required"
                continue

            if (response.status_code in RETRYABLE_STATUS_CODES
                    and attempt < MAX_RETRIES - 1):
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                # Respect Retry-After header if present
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                logger.warning(
                    "API returned %d for model %s (attempt %d/%d). "
                    "Retrying in %.1fs...",
                    response.status_code, model,
                    attempt + 1, MAX_RETRIES, delay,
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 400:
                logger.error(
                    "API error %s for model %s: %s",
                    response.status_code, model, response.text,
                )
            return response

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
            "stream": False,
            **self._max_tokens_param(max_tokens),
        }

        start = time.monotonic()
        response = await self._post_with_retry(
            f"{self.base_url}/chat/completions", payload, model,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = int((time.monotonic() - start) * 1000)

        usage = data.get("usage", {})
        choice = data["choices"][0]
        content = _normalize_content(choice["message"]["content"])
        return AIResponse(
            content=content,
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=elapsed,
            finish_reason=choice.get("finish_reason", ""),
        )

    async def complete_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict:
        """Send a chat completion with tools and return the full message dict.

        Unlike complete(), this returns the raw choices[0].message dict
        which may contain tool_calls in addition to content.

        ``tool_choice`` forces the model to call a specific tool, in
        OpenAI format: ``{"type": "function", "function": {"name": ...}}``.
        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            **self._max_tokens_param(max_tokens),
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        start = time.monotonic()
        response = await self._post_with_retry(
            f"{self.base_url}/chat/completions", payload, model,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = int((time.monotonic() - start) * 1000)

        choice = data["choices"][0]
        message = choice.get("message", {})
        message["content"] = _normalize_content(message.get("content"))
        usage = data.get("usage", {})

        # Some models (e.g. deepseek-reasoner) emit tool calls as DSML
        # markup in content instead of structured tool_calls.  Parse
        # these and promote them so the caller's tool-execution loop works.
        if not message.get("tool_calls") and message.get("content"):
            dsml_calls, remaining = _parse_dsml_tool_calls(message["content"])
            if dsml_calls:
                message["tool_calls"] = dsml_calls
                message["_dsml_tool_calls"] = True  # flag for caller
                message["content"] = remaining

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
            "stream": True,
            **self._max_tokens_param(max_tokens),
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
