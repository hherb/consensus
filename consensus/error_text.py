"""Error text that is safe to show to a model, a transcript or a browser (#92).

An exception's own message can carry an endpoint URL with a credential in its
query string (httpx) or an internal file path (SQLite, which matters in
``--multi-user``). Callers log the exception in full and show only this.
"""

import httpx


class PublicMessageError(Exception):
    """An error whose message we word ourselves, safe to show as it is.

    Raise sites must keep it that way: no raw exception text, and any URL
    passed through ``http_fetch.redact_url``.
    """


def public_error(summary: str, exc: BaseException) -> str:
    """Return *summary* plus what of *exc* is safe to show.

    A :class:`PublicMessageError` keeps its message. Anything else is named
    by class alone, plus the HTTP status of an ``httpx.HTTPStatusError``
    (its message embeds the request URL, so it is never used).

    Args:
        summary: What failed, in our own words (e.g. "Failed to store memory").
        exc: The caught exception.

    Returns:
        ``"<summary>: <message>"`` for a :class:`PublicMessageError`, else
        ``"<summary> (<ExceptionClass>)"`` or
        ``"<summary> (HTTPStatusError, HTTP <code>)"``.
    """
    if isinstance(exc, PublicMessageError):
        return f"{summary}: {exc}"
    name = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{summary} ({name}, HTTP {exc.response.status_code})"
    return f"{summary} ({name})"
