"""Error text that is safe to show to a model, a transcript or a browser (#92).

An exception's own message can carry an endpoint URL with a credential in its
query string (httpx) or an internal file path (SQLite, which matters in
``--multi-user``). Callers log the exception in full and show only this.
"""


def public_error(summary: str, exc: BaseException) -> str:
    """Return *summary* naming the class of *exc*, never its message.

    Args:
        summary: What failed, in our own words (e.g. "Failed to store memory").
        exc: The caught exception; only its class name is used.

    Returns:
        ``"<summary> (<ExceptionClass>)"``.
    """
    return f"{summary} ({type(exc).__name__})"
