"""Typed failures for the document RAG pipeline.

Every defect recorded in issue #78 shares one shape: a failure was made to
look like a value — an error string returned as an answer, pseudo-content
manufactured from a failed extraction, a non-error ``ToolResult``.  Typing
the fault where it happens is what makes that impossible to reintroduce;
the handler layer is the only place these become
``ToolResult(is_error=True)``.

The pattern matches ``models.ConfigurationError`` and
``ai_response.AIResponseFormatError``, established by the flow
error-visibility work (issues #71-#74). Those two live in ``models`` and
``ai_response``, not in ``app_discussion_flow/`` itself.
"""


class DocumentError(Exception):
    """Base for every failure raised inside ``tools_document``.

    Args:
        message: What went wrong, in terms the caller can report.
        hint: An actionable remedy, if one is known — "this looks like a
            scanned PDF; OCR is required" is worth more to a user than a
            traceback.  Appended to ``str()`` when present.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        """Render the message, with the hint appended in parentheses."""
        if self.hint:
            return f"{self.message} ({self.hint})"
        return self.message


class DocumentParseError(DocumentError):
    """Raised when document bytes could not be turned into usable text."""


class DocumentInterpretationError(DocumentError):
    """Raised when an interpretation LLM call could not produce an answer."""


class DocumentIndexError(DocumentError):
    """Raised when a document's chunks could not be embedded."""
