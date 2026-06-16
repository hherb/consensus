"""MCP client over Streamable HTTP — connects to remote MCP servers
using HTTP POST requests with optional SSE streaming responses.

Implements the MCP Streamable HTTP transport specification:
- POST JSON-RPC requests to the server endpoint
- Receive responses as JSON or SSE streams
- Session management via Mcp-Session-Id header
- Automatic session initialization on connect
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Optional

import httpx

from consensus.tools import ToolContext, ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

# Protocol constants
JSONRPC_VERSION = "2.0"
DEFAULT_TIMEOUT = 30.0
MCP_PROTOCOL_VERSION = "2025-03-26"

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0


class MCPHTTPToolProvider(ToolProvider):
    """Tool provider that communicates with a remote MCP server over HTTP.

    Uses the MCP Streamable HTTP transport: JSON-RPC 2.0 over HTTP POST
    with optional SSE streaming for responses.
    """

    def __init__(
        self,
        name: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the HTTP MCP client.

        Args:
            name: Human-readable name for this provider.
            url: The MCP server endpoint URL.
            headers: Additional HTTP headers (e.g. auth tokens).
            timeout: Default request timeout in seconds.
        """
        super().__init__(name)
        self._url = url
        self._extra_headers = headers or {}
        self._timeout = timeout
        self._connected = False
        self._next_id = 1
        self._session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def _headers(self) -> dict[str, str]:
        """Return the extra headers dict (for test assertions)."""
        return self._extra_headers

    def _build_headers(self) -> dict[str, str]:
        """Build the HTTP headers for a request."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._extra_headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def connect(self) -> bool:
        """Initialize a session with the remote MCP server.

        Sends the 'initialize' JSON-RPC request and stores the
        session ID from the response headers.

        Returns:
            True on success, False on failure.
        """
        try:
            self._client = httpx.AsyncClient(timeout=self._timeout)

            init_result = await self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "consensus",
                    "version": "1.0.0",
                },
            })
            logger.info("MCP HTTP server %s initialized: %s",
                        self.name, init_result)

            # Send initialized notification (fire-and-forget)
            await self._send_notification("notifications/initialized", {})

            self._connected = True
            return True
        except Exception:
            logger.exception("Failed to connect to MCP HTTP server %s", self.name)
            await self.close()
            return False

    async def _send_request(
        self,
        method: str,
        params: dict,
        timeout: Optional[float] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Any:
        """Send a JSON-RPC request over HTTP POST and return the result.

        Handles both direct JSON responses and SSE-streamed responses.
        Retries on transient HTTP errors with exponential backoff.

        Args:
            method: JSON-RPC method name.
            params: Parameters dict.
            timeout: Override request timeout (seconds).
            progress_callback: Optional callback(progress, total, message).

        Returns:
            The 'result' field from the JSON-RPC response.

        Raises:
            RuntimeError: On JSON-RPC error responses.
            httpx.HTTPError: On unrecoverable HTTP errors.
        """
        assert self._client is not None

        req_id = self._next_id
        self._next_id += 1

        # Inject progress token if callback provided
        if progress_callback:
            progress_token = str(uuid.uuid4())
            params = {**params, "_meta": {"progressToken": progress_token}}

        body = {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params,
        }

        import asyncio
        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await self._client.post(
                    self._url,
                    json=body,
                    headers=self._build_headers(),
                    timeout=timeout or self._timeout,
                )

                # Store session ID from response
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id

                if response.status_code >= 500 or response.status_code == 429:
                    last_error = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    if attempt < MAX_RETRIES - 1:
                        wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                        logger.warning(
                            "MCP HTTP %s: %d, retrying in %.1fs (attempt %d/%d)",
                            self.name, response.status_code, wait,
                            attempt + 1, MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue
                    # Retries exhausted — raise last_error after the loop.
                    break

                response.raise_for_status()

                content_type = response.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    return self._parse_sse_response(
                        response.text, req_id, progress_callback,
                    )

                # Direct JSON response
                msg = response.json()
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg.get("result")

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "MCP HTTP %s: connection error, retrying in %.1fs",
                        self.name, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Unexpected: no result and no error")

    def _parse_sse_response(
        self,
        text: str,
        req_id: int,
        progress_callback: Optional[Callable] = None,
    ) -> Any:
        """Parse an SSE-formatted response body.

        Extracts JSON-RPC messages from 'data:' lines. Progress
        notifications are forwarded to the callback; the final
        response with the matching request ID is returned.

        Args:
            text: The raw SSE response body.
            req_id: The request ID to match.
            progress_callback: Optional progress callback.

        Returns:
            The 'result' from the matching JSON-RPC response.

        Raises:
            RuntimeError: If no matching response found or on error.
        """
        found = False
        result = None
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str:
                continue
            try:
                msg = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Progress notification
            if msg.get("method") == "notifications/progress" and progress_callback:
                params = msg.get("params", {})
                try:
                    progress_callback(
                        params.get("progress"),
                        params.get("total"),
                        params.get("message", ""),
                    )
                except Exception:
                    logger.debug("Progress callback error", exc_info=True)
                continue

            # Response message
            msg_id = msg.get("id")
            if msg_id == req_id:
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                result = msg.get("result")
                found = True

        if not found:
            raise RuntimeError(
                f"No response found for request {req_id} in SSE stream"
            )
        return result

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        assert self._client is not None
        body = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }
        try:
            await self._client.post(
                self._url,
                json=body,
                headers=self._build_headers(),
                timeout=self._timeout,
            )
        except Exception:
            logger.debug("Notification send failed", exc_info=True)

    async def list_tools(self) -> list[ToolDefinition]:
        """Call tools/list on the remote MCP server."""
        if not self._connected:
            return []

        result = await self._send_request("tools/list", {})
        tools = []
        for t in result.get("tools", []):
            td = ToolDefinition(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("inputSchema", {"type": "object", "properties": {}}),
                provider_name=self.name,
            )
            tools.append(td)
        return tools

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        context: ToolContext,
        progress_callback: Optional[Callable] = None,
    ) -> ToolResult:
        """Call tools/call on the remote MCP server.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments dict.
            context: Execution context.
            progress_callback: Optional callback(progress, total, message).

        Returns:
            ToolResult with the text content from the response.
        """
        if not self._connected:
            return ToolResult(
                content="MCP HTTP server not connected",
                is_error=True,
            )

        try:
            result = await self._send_request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                progress_callback=progress_callback,
            )

            content_parts = result.get("content", [])
            text_parts = []
            for part in content_parts:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

            content = "\n".join(text_parts) if text_parts else ""
            is_error = result.get("isError", False)

            return ToolResult(content=content, is_error=is_error)

        except Exception as e:
            logger.exception("MCP HTTP tool execution failed: %s", tool_name)
            return ToolResult(content=f"MCP HTTP tool error: {e}", is_error=True)

    async def close(self) -> None:
        """Close the HTTP client and clean up resources."""
        self._connected = False

        if self._client:
            # Send session termination if we have a session
            if self._session_id:
                try:
                    await self._client.delete(
                        self._url,
                        headers=self._build_headers(),
                    )
                except Exception:
                    logger.debug("Session termination failed", exc_info=True)
            await self._client.aclose()
            self._client = None

        self._session_id = None
