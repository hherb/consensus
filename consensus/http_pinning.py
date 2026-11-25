"""Pin a guarded fetch's connections to the addresses its guard checked.

``http_fetch.require_public_host`` resolves a host and checks every
address; httpx used to resolve it again to connect, so a DNS answer that
changed in between (rebinding) could reach an internal address (#102).
:func:`pinned_transport` builds an httpx transport whose connections go
only to the addresses recorded for each host.
"""

import logging
from typing import Any
from urllib.request import getproxies

import httpcore
import httpx

logger = logging.getLogger(__name__)

#: Whether the "proxy set, not pinned" notice was logged in this process.
_proxy_notice_logged = False


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


def pinned_transport(pins: dict[str, list[str]]
                     ) -> httpx.AsyncHTTPTransport | None:
    """A transport whose connections go only to addresses in *pins*.

    Returns ``None`` when an environment or system proxy is set for any
    scheme: the client then keeps httpx's stock transport, which honours
    the proxy, and the proxy resolves the name, so there is nothing to pin.
    Any scheme counts, not only the first URL's, because passing a
    transport turns off httpx's env proxies for every hop and a redirect
    can change scheme. The first such fetch logs that pinning is off.

    shortcut: behind a proxy, a host exempted by ``NO_PROXY`` and a scheme
    with no proxy of its own are reached directly and unpinned; mount a
    pinned transport per scheme and mirror httpx's NO_PROXY matching if a
    proxied multi-user deployment needs that closed.
    """
    global _proxy_notice_logged
    proxies = getproxies()
    if any(proxies.get(scheme) for scheme in ("http", "https", "all")):
        if not _proxy_notice_logged:
            _proxy_notice_logged = True
            logger.info("A proxy is configured: guarded fetches are not "
                        "pinned to their checked addresses; DNS rebinding "
                        "is left to the proxy (#102)")
        return None
    transport = httpx.AsyncHTTPTransport()
    # httpx takes no network backend, so its pool is rebuilt with one;
    # tests/test_http_fetch_pinning.py fails if this stops connecting. The
    # pool's own limits apply, which is fine for a client used for one fetch.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        network_backend=PinnedBackend(pins, socket_backend()))
    return transport
