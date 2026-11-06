"""Tests for ``consensus.http_fetch`` — the shared URL fetcher (#88, #89).

Driven through a real ``httpx.AsyncClient`` over ``MockTransport``, so
redirects, request hooks and streaming behave as in production.
"""

import asyncio
import logging

import httpx
import pytest

from consensus import http_fetch
from consensus.http_fetch import (
    FetchError, FetchRejected, FetchTooLarge, FetchUnavailable, fetch_capped,
    redact_url,
)
from tests.image_helpers import install_transport

URL = "https://files.test/a.bin"


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff delays instead of sleeping."""
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


async def _fetch(url=URL, **kwargs):
    kwargs.setdefault("max_bytes", 1000)
    kwargs.setdefault("timeout", 5.0)
    return await fetch_capped(url, **kwargs)


# --- redact_url ------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://h.test/p/x.png", "https://h.test/p/x.png"),
    ("https://h.test/x.png?X-Amz-Signature=abc&t=1", "https://h.test/x.png"),
    ("https://user:pw@h.test:8443/x#frag", "https://h.test:8443/x"),
    ("http://[::1]:80/x?k=v", "http://[::1]:80/x"),
    ("not a url", "not a url"),
    ("http://[::1/x?k=v", http_fetch.UNPARSEABLE_URL),
])
def test_redact_url(url, expected):
    assert redact_url(url) == expected


# --- success / content ---------------------------------------------------------

async def test_returns_body_and_content_type(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=b"abc", headers={"content-type": "image/png"}))
    assert await _fetch() == (b"abc", "image/png")


async def test_missing_content_type_is_empty(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(200, content=b"x"))
    assert await _fetch() == (b"x", "")


# --- retry policy (#88) --------------------------------------------------------

async def test_transient_503_then_200_succeeds(monkeypatch, no_sleep):
    answers = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    seen = install_transport(monkeypatch, lambda r: next(answers))
    content, _ = await _fetch()
    assert content == b"ok"
    assert len(seen) == 2
    assert no_sleep == [http_fetch.FETCH_BASE_DELAY]


async def test_404_is_permanent_and_not_retried(monkeypatch, no_sleep):
    seen = install_transport(monkeypatch, lambda r: httpx.Response(404))
    with pytest.raises(FetchRejected, match="returned HTTP 404"):
        await _fetch()
    assert len(seen) == 1
    assert no_sleep == []


async def test_exhausted_retries_raise_with_last_fault(monkeypatch, no_sleep):
    seen = install_transport(monkeypatch, lambda r: httpx.Response(502))
    with pytest.raises(FetchUnavailable) as info:
        await _fetch(max_retries=3, base_delay=0.5)
    assert len(seen) == 3
    assert no_sleep == [0.5, 1.0]  # exponential, none after the last try
    assert "after 3 attempts: HTTP 502" in str(info.value)


async def test_connection_errors_are_retried(monkeypatch, no_sleep):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, content=b"ok")

    install_transport(monkeypatch, handler)
    assert (await _fetch())[0] == b"ok"


# --- size cap ------------------------------------------------------------------

async def test_declared_oversize_is_rejected(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=b"x" * 20))
    with pytest.raises(FetchTooLarge, match=r"\(20 bytes; the limit is 10\)"):
        await _fetch(max_bytes=10)


async def test_streamed_oversize_is_aborted(monkeypatch):
    produced = []

    async def body():
        for _ in range(100):
            produced.append(1)
            yield b"x" * 10

    install_transport(monkeypatch, lambda r: httpx.Response(200, content=body()))
    with pytest.raises(FetchTooLarge, match="transfer was aborted"):
        await _fetch(max_bytes=25)
    assert len(produced) < 100


# --- guard (SSRF) --------------------------------------------------------------

def _refuse_internal(url: str) -> None:
    if "internal" in url:
        raise FetchRejected("internal address refused")


async def test_guard_refuses_first_request(monkeypatch):
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    with pytest.raises(FetchRejected, match="internal address refused"):
        await _fetch("http://internal.test/x", guard=_refuse_internal)
    assert seen == []


async def test_guard_checks_every_redirect_hop(monkeypatch):
    def handler(request):
        if request.url.host == "public.test":
            return httpx.Response(
                302, headers={"location": "http://internal.test/meta"})
        return httpx.Response(200, content=b"secret")

    seen = install_transport(monkeypatch, handler)
    with pytest.raises(FetchRejected, match="internal address refused"):
        await _fetch("http://public.test/x", guard=_refuse_internal)
    assert [r.url.host for r in seen] == ["public.test"]


async def test_redirects_followed_without_guard(monkeypatch):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, content=b"moved")

    install_transport(monkeypatch, handler)
    assert (await _fetch("https://files.test/old"))[0] == b"moved"


async def test_redirect_loop_is_permanent(monkeypatch, no_sleep):
    install_transport(monkeypatch, lambda r: httpx.Response(
        302, headers={"location": str(r.url)}))
    with pytest.raises(FetchRejected, match="too many redirects"):
        await _fetch()
    assert no_sleep == []


# --- privacy of logs and messages (#89) ----------------------------------------

async def test_messages_and_logs_redact_secrets(monkeypatch, no_sleep, caplog):
    install_transport(monkeypatch, lambda r: httpx.Response(503))
    secret = "https://u:pw@files.test/a?token=SECRET"
    with caplog.at_level(logging.WARNING, logger="consensus.http_fetch"), \
            pytest.raises(FetchError) as info:
        await _fetch(secret, max_retries=2)
    for text in (caplog.text, str(info.value)):
        assert "SECRET" not in text and "pw" not in text
    assert "https://files.test/a" in caplog.text
