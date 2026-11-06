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
    secret = "https://u:PWSECRET@files.test/a?token=SECRET"
    with caplog.at_level(logging.WARNING, logger="consensus.http_fetch"), \
            pytest.raises(FetchError) as info:
        await _fetch(secret, max_retries=2)
    for text in (caplog.text, str(info.value)):
        assert "SECRET" not in text and "PWSECRET" not in text
    assert "https://files.test/a" in caplog.text


# --- permanent transport faults (PR review) -------------------------------------

@pytest.mark.parametrize("fault", [
    httpx.UnsupportedProtocol("no scheme"),
    httpx.DecodingError("bad gzip"),
    httpx.InvalidURL("bad https://h.test/x?token=SECRET"),
])
async def test_permanent_faults_are_rejected_without_retry(monkeypatch,
                                                           no_sleep, fault):
    calls = []

    def handler(request):
        calls.append(request)
        raise fault

    install_transport(monkeypatch, handler)
    with pytest.raises(FetchRejected) as info:
        await _fetch("https://h.test/x?token=SECRET")
    assert len(calls) == 1 and no_sleep == []
    assert "SECRET" not in str(info.value)
    assert type(fault).__name__ in str(info.value)


async def test_garbage_content_length_falls_back_to_streamed_cap(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=b"ok", headers={"content-length": "lots"}))
    assert (await _fetch())[0] == b"ok"


# --- status policy (PR #100 review) ---------------------------------------------

@pytest.mark.parametrize("status, retryable", [
    (408, True), (425, True), (429, True), (500, True), (502, True),
    (503, True), (504, True), (520, True),
    (400, False), (401, False), (403, False), (404, False), (410, False),
    (501, False), (505, False), (302, False), (304, False), (600, False),
])
def test_is_retryable_status(status, retryable):
    assert http_fetch.is_retryable_status(status) is retryable


async def test_429_is_retried(monkeypatch, no_sleep):
    answers = iter([httpx.Response(429), httpx.Response(200, content=b"ok")])
    seen = install_transport(monkeypatch, lambda r: next(answers))
    assert (await _fetch())[0] == b"ok"
    assert len(seen) == 2


@pytest.mark.parametrize("status", [304, 501])
async def test_unfollowed_3xx_and_501_are_permanent(monkeypatch, no_sleep,
                                                    status):
    seen = install_transport(monkeypatch, lambda r: httpx.Response(status))
    with pytest.raises(FetchRejected, match=f"HTTP {status}"):
        await _fetch()
    assert len(seen) == 1 and no_sleep == []


# --- streaming edge cases --------------------------------------------------------

class _StallingStream(httpx.AsyncByteStream):
    """A body that sends one chunk, then times out."""

    async def __aiter__(self):
        yield b"partial"
        raise httpx.ReadTimeout("stalled")


async def test_mid_stream_fault_is_retried_from_scratch(monkeypatch, no_sleep):
    answers = iter([httpx.Response(200, stream=_StallingStream()),
                    httpx.Response(200, content=b"whole")])
    install_transport(monkeypatch, lambda r: next(answers))
    # The partial chunk of the failed attempt must not leak into the result.
    assert (await _fetch())[0] == b"whole"
    assert no_sleep == [http_fetch.FETCH_BASE_DELAY]


async def test_body_of_exactly_max_bytes_is_accepted(monkeypatch):
    async def body():
        yield b"x" * 600
        yield b"x" * 400

    install_transport(monkeypatch, lambda r: httpx.Response(200, content=body()))
    assert len((await _fetch(max_bytes=1000))[0]) == 1000


async def test_understated_content_length_is_caught_by_the_stream_cap(
        monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, stream=httpx.ByteStream(b"x" * 500),
        headers={"content-length": "2"}))
    with pytest.raises(FetchTooLarge, match="transfer was aborted"):
        await _fetch(max_bytes=100)


# --- deadline and headers --------------------------------------------------------

async def test_deadline_bounds_the_whole_fetch(monkeypatch):
    async def hang(request):
        await asyncio.sleep(30)

    install_transport(monkeypatch, hang)
    with pytest.raises(FetchUnavailable, match="within 0.05s"):
        await _fetch(deadline=0.05)


async def test_extra_headers_are_sent(monkeypatch):
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    await _fetch(headers={"User-Agent": "TestBot/1"})
    assert seen[0].headers["user-agent"] == "TestBot/1"
