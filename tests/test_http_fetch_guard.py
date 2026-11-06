"""Tests for the SSRF guard ``require_public_host`` and the callers it protects.

Document and webpage fetches had no guard at all (PR #100 review), so an
AI participant could ``doc_add`` a cloud-metadata URL and read the answer
back. IP literals go through the real resolver (no network is needed for a
literal); named hosts use ``fake_dns``.
"""

import asyncio
import logging

import httpx
import pytest

from consensus import tools_builtin
from consensus.http_fetch import (
    FETCH_MAX_RETRIES, FetchRejected, FetchUnavailable, fetch_capped,
    is_public_ip, require_public_host,
)
from consensus.tools import ToolContext
from consensus.tools_document import parsing
from consensus.tools_document.errors import DocumentParseError
from tests.image_helpers import PUBLIC_IP, fake_dns, install_transport


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip backoff delays."""
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


# --- the guard itself -----------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest",
    "http://10.0.0.1/", "http://[fe80::1]/", "http://[fd00::1]/",
    "http://[ff02::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[64:ff9b::7f00:1]/",   # NAT64 wrapping loopback
    "http://[::7f00:1]/",          # IPv4-compatible loopback
    "http://[64:ff9b::a9fe:a9fe]/",  # NAT64 wrapping cloud metadata
])
def test_internal_ip_literals_are_refused(url):
    with pytest.raises(FetchRejected, match="private/internal"):
        require_public_host(url)


@pytest.mark.parametrize("url", [
    f"http://{PUBLIC_IP}/", "http://[64:ff9b::5db8:d822]/",  # NAT64 of public
])
def test_public_ip_literals_pass(url):
    require_public_host(url)


@pytest.mark.parametrize("answers", [
    [PUBLIC_IP, "10.0.0.1"], ["10.0.0.1", PUBLIC_IP],
])
def test_any_internal_record_refuses_the_host(monkeypatch, answers):
    """Checking only the first DNS record would let a mixed answer through."""
    fake_dns(monkeypatch, {"mixed.test": answers})
    with pytest.raises(FetchRejected, match="mixed.test"):
        require_public_host("http://mixed.test/x")


def test_url_without_host_is_refused():
    with pytest.raises(FetchRejected, match="has no host"):
        require_public_host("http:///x")


def test_unresolvable_host_is_transient_not_an_ssrf_refusal(monkeypatch):
    fake_dns(monkeypatch, fail={"nowhere.test"})
    with pytest.raises(FetchUnavailable, match="could not resolve"):
        require_public_host("http://nowhere.test/x")


def test_is_public_ip_rejects_multicast():
    import ipaddress
    assert not is_public_ip(ipaddress.ip_address("224.0.0.1"))
    assert is_public_ip(ipaddress.ip_address(PUBLIC_IP))


async def test_dns_failure_is_retried_through_fetch_capped(monkeypatch,
                                                           no_sleep):
    looked_up = fake_dns(monkeypatch, fail={"flaky.test"})
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    with pytest.raises(FetchUnavailable,
                       match=f"after {FETCH_MAX_RETRIES} attempts"):
        await fetch_capped("http://flaky.test/x", max_bytes=10, timeout=1.0,
                           guard=require_public_host)
    assert looked_up == ["flaky.test"] * FETCH_MAX_RETRIES and seen == []


# --- document ingestion is guarded ----------------------------------------------

async def test_document_fetch_refuses_an_internal_host(monkeypatch):
    fake_dns(monkeypatch, {"metadata.test": "169.254.169.254"})
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    with pytest.raises(DocumentParseError, match="private/internal") as info:
        await parsing.fetch_url_content("http://metadata.test/latest")
    assert seen == []
    assert "public http(s) URL" in info.value.hint


async def test_document_fetch_refuses_a_redirect_to_an_internal_host(
        monkeypatch):
    fake_dns(monkeypatch, {"internal.test": "10.0.0.1"})

    def handler(request):
        return httpx.Response(302, headers={"location": "http://internal.test/"})

    seen = install_transport(monkeypatch, handler)
    with pytest.raises(DocumentParseError, match="private/internal"):
        await parsing.fetch_url_content("http://public.test/doc")
    assert [r.url.host for r in seen] == ["public.test"]


async def test_unlisted_fetch_error_gets_the_default_hint(monkeypatch):
    """An exact-type lookup raised KeyError for an unlisted FetchError."""
    from consensus.http_fetch import FetchError

    async def raises(*args, **kwargs):
        raise FetchError("odd failure")

    monkeypatch.setattr(parsing, "fetch_capped", raises)
    with pytest.raises(DocumentParseError) as info:
        await parsing.fetch_url_content("http://x.test/")
    assert info.value.hint == parsing._FETCH_HINT_DEFAULT


# --- fetch_webpage is guarded ---------------------------------------------------

async def _fetch_page(url):
    return await tools_builtin.fetch_webpage_handler(
        {"url": url}, ToolContext(caller_entity_id=1, discussion_id=0))


async def test_webpage_fetch_refuses_an_internal_host(monkeypatch, caplog):
    fake_dns(monkeypatch, {"metadata.test": "169.254.169.254"})
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    with caplog.at_level(logging.WARNING, logger="consensus.tools_builtin"):
        result = await _fetch_page("http://metadata.test/latest?token=SECRET")
    assert result.is_error and "private/internal" in result.content
    assert seen == []
    assert "private/internal" in caplog.text and "SECRET" not in caplog.text


async def test_webpage_fetch_extracts_text_and_sends_user_agent(monkeypatch):
    fake_dns(monkeypatch)
    seen = install_transport(monkeypatch, lambda r: httpx.Response(
        200, content="<html><body><p>Grüße aus dem Netz</p></body></html>"
        .encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"}))
    result = await _fetch_page("https://page.test/a")
    assert not result.is_error
    assert "Grüße aus dem Netz" in result.content
    assert seen[0].headers["user-agent"] == tools_builtin.WEB_FETCH_USER_AGENT
