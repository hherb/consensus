"""DNS-rebinding protection: a guarded fetch connects to the checked address.

``require_public_host`` resolves the host and checks every address. httpx
used to resolve it a second time, so a DNS answer that changed between the
two lookups (rebinding) could reach an internal address (#102). Now the
guard returns the addresses it checked and the connection is pinned to
them. These tests replace the socket layer under httpcore, so httpx, its
request hooks and httpcore's connection pool all run for real.
"""

import asyncio
import socket
import ssl

import httpcore
import httpx
import pytest

from consensus import http_pinning
from consensus.http_fetch import FetchRejected, fetch_capped, require_public_host
from tests.image_helpers import PUBLIC_IP, fake_dns

OTHER_PUBLIC_IP = "93.184.216.35"
REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nok"


class RecordingBackend(httpcore.AsyncMockBackend):
    """Answers each connection with *REPLY*, recording where it connected.

    With *replies*, the n-th connection gets the n-th reply instead. Hosts
    listed in *refuse* raise ``httpcore.ConnectError``.
    """

    def __init__(self, refuse: set[str] | None = None,
                 replies: list[bytes] | None = None) -> None:
        super().__init__([REPLY])
        self.connected: list[tuple[str, int]] = []
        self.refuse = refuse or set()
        self.replies = list(replies or [])

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        self.connected.append((host, port))
        if host in self.refuse:
            raise httpcore.ConnectError(f"refused {host}")
        if self.replies:
            return httpcore.AsyncMockStream([self.replies.pop(0)])
        return await super().connect_tcp(host, port, timeout, local_address,
                                         socket_options)


@pytest.fixture
def wire(monkeypatch):
    """Replace the real socket backend under the pinned transport."""
    backend = RecordingBackend()
    monkeypatch.setattr(http_pinning, "socket_backend", lambda: backend)
    monkeypatch.setattr(http_pinning, "getproxies", dict)
    return backend


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip backoff delays."""
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def test_guard_returns_every_checked_address_once(monkeypatch):
    fake_dns(monkeypatch, {"multi.test": [PUBLIC_IP, OTHER_PUBLIC_IP,
                                          PUBLIC_IP]})
    assert require_public_host("http://multi.test/x") == [
        PUBLIC_IP, OTHER_PUBLIC_IP]


async def test_fetch_connects_to_the_checked_address(monkeypatch, wire):
    """httpx must not resolve the host again; the second answer is internal."""
    looked_up: list[str] = []

    def rebinding(host, *args, **kwargs):
        looked_up.append(host)
        ip = PUBLIC_IP if len(looked_up) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    body, content_type = await fetch_capped(
        "http://rebind.test:8080/x", max_bytes=100, timeout=1.0,
        guard=require_public_host)
    assert (body, content_type) == (b"ok", "text/plain")
    assert wire.connected == [(PUBLIC_IP, 8080)]
    assert looked_up == ["rebind.test"]


async def test_tls_keeps_the_hostname_for_sni(monkeypatch, wire):
    """Pinning swaps the address only; the certificate is checked for the name."""
    started: list[tuple[str | None, ssl.SSLContext]] = []
    real_connect = RecordingBackend.connect_tcp

    async def connect(self, *args, **kwargs):
        stream = await real_connect(self, *args, **kwargs)
        real_start_tls = stream.start_tls

        async def start_tls(ssl_context, server_hostname=None, timeout=None):
            started.append((server_hostname, ssl_context))
            return await real_start_tls(ssl_context, server_hostname, timeout)

        stream.start_tls = start_tls
        return stream

    monkeypatch.setattr(RecordingBackend, "connect_tcp", connect)
    fake_dns(monkeypatch, {"secure.test": PUBLIC_IP})
    await fetch_capped("https://secure.test/x", max_bytes=100, timeout=1.0,
                       guard=require_public_host)
    assert wire.connected == [(PUBLIC_IP, 443)]
    [(name, context)] = started
    assert name == "secure.test"
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


async def test_next_checked_address_is_tried_when_one_refuses(monkeypatch,
                                                              wire):
    wire.refuse = {PUBLIC_IP}
    fake_dns(monkeypatch, {"two.test": [PUBLIC_IP, OTHER_PUBLIC_IP]})
    body, _ = await fetch_capped("http://two.test/x", max_bytes=100,
                                 timeout=1.0, guard=require_public_host)
    assert body == b"ok"
    assert wire.connected == [(PUBLIC_IP, 80), (OTHER_PUBLIC_IP, 80)]


async def test_each_redirect_hop_connects_to_its_own_checked_address(
        monkeypatch):
    """A hop's host is pinned by its own guard check, never looked up again."""
    backend = RecordingBackend(replies=[
        b"HTTP/1.1 302 Found\r\nLocation: http://b.test/y\r\n"
        b"Content-Length: 0\r\n\r\n", REPLY])
    monkeypatch.setattr(http_pinning, "socket_backend", lambda: backend)
    monkeypatch.setattr(http_pinning, "getproxies", dict)
    looked_up: list[str] = []

    def dns(host, *args, **kwargs):
        looked_up.append(host)
        ip = {"a.test": PUBLIC_IP, "b.test": OTHER_PUBLIC_IP}[host]
        if looked_up.count(host) > 1:
            ip = "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    body, _ = await fetch_capped("http://a.test/x", max_bytes=100,
                                 timeout=1.0, guard=require_public_host)
    assert body == b"ok"
    assert backend.connected == [(PUBLIC_IP, 80), (OTHER_PUBLIC_IP, 80)]
    assert looked_up == ["a.test", "b.test"]


async def test_a_host_the_guard_never_checked_is_not_connected():
    backend = http_pinning.PinnedBackend({}, RecordingBackend())
    with pytest.raises(httpcore.ConnectError, match="not checked"):
        await backend.connect_tcp("unchecked.test", 80)


async def test_a_guard_returning_no_addresses_cannot_connect(monkeypatch, wire,
                                                             no_sleep):
    """A guard that checked nothing is a programming fault: not retried."""
    calls: list[str] = []

    def guard(url):
        calls.append(url)

    with pytest.raises(FetchRejected, match="no address"):
        await fetch_capped("http://plain.test/x", max_bytes=100, timeout=1.0,
                           guard=guard)
    assert wire.connected == []
    assert len(calls) == 1


async def test_an_env_proxy_keeps_the_stock_transport(monkeypatch):
    """Behind a proxy the proxy resolves the name, so there is nothing to pin.

    Any scheme's proxy counts: a transport would switch off httpx's env
    proxies for every hop, and a redirect can change the scheme.
    """
    monkeypatch.setattr(http_pinning, "getproxies",
                        lambda: {"https": "http://proxy.test:3128"})
    assert http_pinning.pinned_transport({}) is None
    monkeypatch.setattr(http_pinning, "getproxies",
                        lambda: {"no": "localhost"})
    assert http_pinning.pinned_transport({}) is not None
    monkeypatch.setattr(http_pinning, "getproxies",
                        lambda: {"all": "socks5://proxy.test:1080"})
    assert http_pinning.pinned_transport({}) is None


async def test_unguarded_fetch_is_not_pinned(monkeypatch):
    """The pricing download passes no guard and keeps httpx's own resolver."""
    built: list[dict] = []
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        built.append(kwargs)
        return real_client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"x")), **{
                k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    await fetch_capped("http://plain.test/x", max_bytes=10, timeout=1.0)
    assert built and "transport" not in built[0]
