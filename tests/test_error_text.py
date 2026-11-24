"""Tests for ``consensus.error_text`` — error text safe to show (#92)."""

import httpx

from consensus.error_text import PublicMessageError, public_error

#: Text an exception may carry that must never reach the model or the UI.
SECRET = "http://u:pw@embed.local/api?token=s3cret"


def test_an_arbitrary_exception_is_named_by_class_only():
    assert public_error("Lookup failed", RuntimeError(SECRET)) == (
        "Lookup failed (RuntimeError)")


def test_an_http_status_error_adds_its_status_but_not_its_url():
    request = httpx.Request("GET", SECRET)
    exc = httpx.HTTPStatusError(
        str(SECRET), request=request,
        response=httpx.Response(401, request=request))
    assert public_error("Fetch failed", exc) == (
        "Fetch failed (HTTPStatusError, HTTP 401)")


def test_a_message_we_wrote_ourselves_is_shown():
    class Ours(PublicMessageError):
        pass
    assert public_error("Search failed", Ours("the index is empty")) == (
        "Search failed: the index is empty")
