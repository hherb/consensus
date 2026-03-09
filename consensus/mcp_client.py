"""MCP (Model Context Protocol) client — communicates with MCP server
subprocesses over stdin/stdout using JSON-RPC 2.0.

Implements MCPToolProvider which extends ToolProvider to provide tools
from external MCP servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Optional

from consensus.tools import ToolContext, ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

# Protocol constants
JSONRPC_VERSION = "2.0"
DEFAULT_TIMEOUT = 30.0
MCP_PROTOCOL_VERSION = "2024-11-05"


def encode_jsonrpc_request(method: str, params: dict, req_id: int | str) -> str:
    """Create a JSON-RPC 2.0 request string.

    Args:
        method: The JSON-RPC method name (e.g. "tools/list").
        params: Parameters dict for the request.
        req_id: Unique request identifier.

    Returns:
        JSON string (without trailing newline).
    """
    msg = {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "method": method,
        "params": params,
    }
    return json.dumps(msg)


class MCPToolProvider(ToolProvider):
    """Tool provider that communicates with an MCP server subprocess.

    Launches the server as a child process, communicates over stdin/stdout
    using newline-delimited JSON-RPC 2.0 messages.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(name)
        self._command = command
        self._args = args
        self._env = env
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._connected = False
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future] = {}
        self._progress_callbacks: dict[str, Callable] = {}

    async def connect(self) -> bool:
        """Launch the MCP server subprocess and perform the initialize handshake.

        Returns True on success, False on failure.
        """
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            logger.info("Launched MCP server %s (pid=%s)", self.name, self._process.pid)

            # Start the read loop
            self._reader_task = asyncio.create_task(self._read_loop())

            # Send initialize handshake
            init_result = await self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "consensus",
                    "version": "1.0.0",
                },
            })
            logger.info("MCP server %s initialized: %s", self.name, init_result)

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})
            self._connected = True
            return True
        except Exception:
            logger.exception("Failed to connect to MCP server %s", self.name)
            await self.close()
            return False

    async def _read_loop(self) -> None:
        """Read newline-delimited JSON-RPC messages from the subprocess stdout."""
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # EOF — process exited
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from MCP server %s: %s",
                                   self.name, line_str[:200])
                    continue
                self._handle_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Read loop error for MCP server %s", self.name)

    def _handle_message(self, msg: dict) -> None:
        """Route a received JSON-RPC message.

        - Responses (have "id"): resolve the matching pending future.
        - Notifications (no "id"): handle progress notifications etc.
        """
        # Check if this is a response (has "id" and either "result" or "error")
        msg_id = msg.get("id")
        if msg_id is not None and ("result" in msg or "error" in msg):
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(
                        RuntimeError(f"MCP error: {msg['error']}")
                    )
                else:
                    future.set_result(msg.get("result"))
            return

        # Check for notifications
        method = msg.get("method")
        if method == "notifications/progress":
            params = msg.get("params", {})
            token = params.get("progressToken")
            if token and token in self._progress_callbacks:
                cb = self._progress_callbacks[token]
                try:
                    cb(
                        params.get("progress"),
                        params.get("total"),
                        params.get("message", ""),
                    )
                except Exception:
                    logger.debug("Progress callback error", exc_info=True)

    async def _send_request(
        self,
        method: str,
        params: dict,
        timeout: Optional[float] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Any:
        """Send a JSON-RPC request and await the response.

        Args:
            method: JSON-RPC method name.
            params: Parameters dict.
            timeout: Response timeout in seconds (default: DEFAULT_TIMEOUT).
            progress_callback: Optional callback(progress, total, message)
                for progress notifications tied to this request.

        Returns:
            The "result" field from the JSON-RPC response.
        """
        assert self._process and self._process.stdin

        req_id = self._next_id
        self._next_id += 1

        # Set up progress token if callback provided
        progress_token = None
        if progress_callback:
            progress_token = str(uuid.uuid4())
            params = {**params, "_meta": {"progressToken": progress_token}}
            self._progress_callbacks[progress_token] = progress_callback

        msg = encode_jsonrpc_request(method, params, req_id)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future

        try:
            self._process.stdin.write((msg + "\n").encode("utf-8"))
            await self._process.stdin.drain()

            return await asyncio.wait_for(
                future, timeout=timeout or DEFAULT_TIMEOUT
            )
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise
        finally:
            if progress_token:
                self._progress_callbacks.pop(progress_token, None)

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        assert self._process and self._process.stdin
        msg = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "params": params,
        }
        data = json.dumps(msg) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    async def list_tools(self) -> list[ToolDefinition]:
        """Call tools/list on the MCP server and return ToolDefinitions."""
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
        """Call tools/call on the MCP server.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments dict.
            context: Execution context (caller, discussion, access mode).
            progress_callback: Optional callback(progress, total, message).

        Returns:
            ToolResult with the text content from the MCP response.
        """
        if not self._connected:
            return ToolResult(
                content="MCP server not connected",
                is_error=True,
            )

        try:
            result = await self._send_request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                progress_callback=progress_callback,
            )

            # Extract text content from the MCP response
            content_parts = result.get("content", [])
            text_parts = []
            for part in content_parts:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

            content = "\n".join(text_parts) if text_parts else ""
            is_error = result.get("isError", False)

            return ToolResult(content=content, is_error=is_error)

        except Exception as e:
            logger.exception("MCP tool execution failed: %s", tool_name)
            return ToolResult(content=f"MCP tool error: {e}", is_error=True)

    async def close(self) -> None:
        """Cancel the reader task and terminate the subprocess."""
        self._connected = False

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None

        # Cancel any pending futures
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self._progress_callbacks.clear()
