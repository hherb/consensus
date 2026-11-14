"""The exception a tool handler raises for an expected failure."""


class ToolError(Exception):
    """A tool call failed for a reason the caller can act on.

    A value the handler checks and rejects (a blank query, a non-AI
    entity), a missing record, an over-long query, or an unavailable
    embedding service. ``_handle_tool_call`` turns it into an MCP
    ``isError`` result reading ``Error: <message>`` and logs it at WARNING
    without a traceback; any other exception is treated as a bug and logged
    with one. That includes an argument of the wrong type or a missing
    required one, which no handler validates yet.
    """
