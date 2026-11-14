"""The exception a tool handler raises for an expected failure."""


class ToolError(Exception):
    """A tool call failed for a reason the caller can act on.

    Bad arguments, a missing record, or an unavailable embedding service.
    ``_handle_tool_call`` turns it into an MCP ``isError`` result reading
    ``Error: <message>`` and logs it at WARNING without a traceback;
    any other exception is treated as a bug and logged with one.
    """
