"""Pin a guarded fetch's connections to the addresses its guard checked.

``http_fetch.require_public_host`` resolves a host and checks every
address; httpx used to resolve it again to connect, so a DNS answer that
changed in between (rebinding) could reach an internal address (#102).
:func:`pinned_transport` builds an httpx transport whose connections go
only to the addresses recorded for each host.
"""

from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpcore
import httpx


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects a host only to the addresses the fetch's guard checked.

    httpcore hands :meth:`connect_tcp` the request's own host, the same
    ``raw_host`` the guard hook recorded; TLS still verifies the certificate
    against that name, because httpcore takes the SNI name from the origin,
    not from the address connected to.
    """

    def __init__(self, pins: dict[str, list[str]],
                 inner: httpcore.AsyncNetworkBackend) -> None:
        self._pins = pins
        self._inner = inner

    async def connect_tcp(self, host: str, port: int,
                          timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: Any = None,
                          ) -> httpcore.AsyncNetworkStream:
        """Connect to the first checked address of *host* that answers."""
        addresses = self._pins.get(host)
        if not addresses:
            raise httpcore.ConnectError(
                f"{host} was not checked by the URL guard")
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        raise last  # type: ignore[misc]  # addresses was non-empty

    async def connect_unix_socket(self, path: str,
                                  timeout: float | None = None,
                                  socket_options: Any = None,
                                  ) -> httpcore.AsyncNetworkStream:
        """Refuse: a guarded fetch only ever reaches checked TCP hosts."""
        raise httpcore.ConnectError("Unix sockets are not fetched")

    async def sleep(self, seconds: float) -> None:
        """Delegate to the wrapped backend."""
        await self._inner.sleep(seconds)


def socket_backend() -> httpcore.AsyncNetworkBackend:
    """The real network backend under a pinned transport (tests replace it)."""
    return httpcore.AnyIOBackend()


def pinned_transport(url: str, pins: dict[str, list[str]]
                      ) -> httpx.AsyncHTTPTransport | None:
    """A transport whose connections go only to addresses in *pins*.

    Returns ``None`` when an environment or system proxy applies to *url*'s
    scheme: the client then keeps httpx's stock transport, which honours
    the proxy, and the proxy resolves the name, so there is nothing to pin.

    shortcut: a host exempted by ``NO_PROXY`` while a proxy is set is
    reached directly and unpinned; mirror httpx's NO_PROXY matching if a
    proxied multi-user deployment needs that closed.
    """
    proxies = getproxies()
    if proxies.get(urlsplit(url).scheme) or proxies.get("all"):
        return None
    transport = httpx.AsyncHTTPTransport()
    # httpx takes no network backend, so its pool is rebuilt with one;
    # tests/test_http_fetch_pinning.py fails if this stops connecting.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        network_backend=PinnedBackend(pins, socket_backend()))
    return transport
