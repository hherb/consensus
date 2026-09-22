"""Pure parsing helpers for OpenAI-compatible completion responses.

Split out of ``ai_client`` (golden rules 1 and 8): these are side-effect
free functions over a response body, which makes them independently
testable and keeps the client module focused on transport and retries.

The central rule here is that a provider's misbehaviour must be *typed*
at the point it is detected.  ``data["choices"][0]`` on a malformed body
raises a bare ``KeyError`` that is indistinguishable from a bug inside
Consensus, and reporting someone else's broken gateway as our own fault
sends debugging in exactly the wrong direction (issue #74).
"""

import httpx


class AIResponseFormatError(Exception):
    """A provider returned a success status with an unusable body.

    Gateways and proxies routinely answer HTTP 200 with a non-JSON body or
    with ``{"error": ...}`` in place of ``choices``.  Parsing that raises
    ``JSONDecodeError``/``KeyError``/``IndexError``, which are
    indistinguishable from a bug inside Consensus unless they are typed at
    the point they occur — and misreporting a provider fault as our own
    bug sends debugging in exactly the wrong direction (issue #74).
    """


#: How much of an unusable response body to quote back to the user.  Long
#: enough to show a gateway's HTML error title or an embedded ``error``
#: object, short enough to stay readable in a transcript notice.
MALFORMED_BODY_EXCERPT_LENGTH = 200


def _parse_completion_body(response: httpx.Response, model: str) -> dict:
    """Decode a completion response body, or raise ``AIResponseFormatError``.

    ``raise_for_status`` has already passed at this point, so a decode
    failure here means the provider answered "success" with something that
    is not JSON — typically a proxy's HTML error page.
    """
    try:
        data = response.json()
    except ValueError as e:
        excerpt = response.text[:MALFORMED_BODY_EXCERPT_LENGTH].strip()
        raise AIResponseFormatError(
            f"{model} returned HTTP {response.status_code} with a body that "
            f"is not JSON: {excerpt}",
        ) from e
    if not isinstance(data, dict):
        raise AIResponseFormatError(
            f"{model} returned HTTP {response.status_code} with a JSON "
            f"{type(data).__name__} where an object was expected",
        )
    return data


def _malformed_body_detail(model: str, data: dict, e: Exception) -> str:
    """Describe a decoded body that carried no usable completion.

    Providers that fail mid-stream commonly return ``{"error": {...}}`` with
    a 200 status; that nested message is the actionable part, so it is
    preferred over the bare ``KeyError`` when present.
    """
    embedded = data.get("error")
    if embedded:
        if isinstance(embedded, dict):
            embedded = embedded.get("message", embedded)
        return f"{model} reported an error in a success response: {embedded}"
    return (
        f"{model} returned a response with no usable choices "
        f"({type(e).__name__}: {e})"
    )
