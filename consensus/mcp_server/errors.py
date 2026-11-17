"""The exception a tool handler raises for an expected failure."""


class ToolError(Exception):
    """A tool call failed for a reason the caller can act on.

    A value the handler checks and rejects (a blank query, a non-AI
    entity), an argument that does not match the tool's ``inputSchema``
    (``validation.validate_arguments``), a missing record, an over-long
    query, or an unavailable embedding service. ``_handle_tool_call`` turns
    it into an MCP ``isError`` result reading ``Error: <message>`` and logs
    it at WARNING without a traceback; any other exception is treated as a
    bug and logged with one.
    """
