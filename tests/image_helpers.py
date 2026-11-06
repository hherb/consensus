"""Shared fakes for the ``consensus.tools_image`` tests.

Every fake sits at a network or process edge (DNS, HTTP, the AI client,
the pricing cache) so the tests drive the real storage, database and
handler code. Patching goes through :func:`patch_image_module`, which
rebinds a name in *every* ``consensus.tools_image*`` module that holds it,
so the tests stay valid whether ``tools_image`` is one module or a package
(issue #61): a ``from .x import y`` binding lives in the importing module,
and patching only the facade would silently have no effect.
"""

import io
import socket
import sys
from types import SimpleNamespace

import httpx

IMAGE_MODULE_PREFIX = "consensus.tools_image"

#: The address the fake resolver returns for any host it is not told about.
PUBLIC_IP = "93.184.216.34"


def patch_image_module(monkeypatch, name: str, replacement) -> int:
    """Rebind *name* in every loaded ``consensus.tools_image*`` module.

    Returns:
        How many modules were patched; asserting it is non-zero catches a
        name that no longer exists anywhere (a stale patch is a no-op).
    """
    patched = 0
    for mod_name, module in list(sys.modules.items()):
        if (mod_name == IMAGE_MODULE_PREFIX
                or mod_name.startswith(IMAGE_MODULE_PREFIX + ".")):
            if module is not None and hasattr(module, name):
                monkeypatch.setattr(module, name, replacement)
                patched += 1
    assert patched, f"{name} is not bound in any tools_image module"
    return patched


def make_image(width: int, height: int, fmt: str = "PNG",
               mode: str = "RGB") -> bytes:
    """Encode a solid *width* x *height* image with Pillow."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, (width, height)).save(buf, format=fmt)
    return buf.getvalue()


def image_size(content: bytes) -> tuple[int, int]:
    """Decode *content* with Pillow and return its ``(width, height)``."""
    from PIL import Image

    return Image.open(io.BytesIO(content)).size


def image_format(content: bytes) -> str:
    """Decode *content* with Pillow and return its format name."""
    from PIL import Image

    return Image.open(io.BytesIO(content)).format


def fake_dns(monkeypatch, addresses: dict[str, str] | None = None,
             fail: set[str] | None = None) -> list[str]:
    """Replace ``socket.getaddrinfo`` with a scripted resolver.

    Args:
        addresses: hostname -> IP. Unlisted hosts resolve to ``PUBLIC_IP``.
        fail: hostnames that raise ``socket.gaierror``.

    Returns:
        The list of hostnames looked up, in order.
    """
    addresses = addresses or {}
    fail = fail or set()
    looked_up: list[str] = []

    def getaddrinfo(host, port, *args, **kwargs):
        looked_up.append(host)
        if host in fail:
            raise socket.gaierror("no such host")
        ip = addresses.get(host, PUBLIC_IP)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return looked_up


def install_transport(monkeypatch, handler) -> list[httpx.Request]:
    """Route every ``httpx.AsyncClient`` through a ``MockTransport``.

    The real client is kept, so redirects, event hooks and streaming
    behave as in production; only the wire is replaced.

    Returns:
        Every request the handler saw, in order.
    """
    real_client = httpx.AsyncClient
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(recording), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


class FakePricing:
    """Stand-in for ``PricingCache.has_input_modality`` (no network)."""

    def __init__(self, vision: dict[str, bool | None] | None = None) -> None:
        self.vision = vision or {}

    def has_input_modality(self, model: str, modality: str,
                           base_url: str = "") -> bool | None:
        """Return the scripted answer for *model*, ``None`` if unknown."""
        return self.vision.get(model)


class FakeVisionClient:
    """Stand-in for ``AIClient`` recording construction and calls."""

    instances: list["FakeVisionClient"] = []
    reply = "A red square."
    error: Exception | None = None

    def __init__(self, base_url: str, api_key: str = "", **kwargs) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.calls: list[dict] = []
        self.closed = False
        FakeVisionClient.instances.append(self)

    async def complete(self, **kwargs):
        """Record the call; return the scripted reply or raise."""
        self.calls.append(kwargs)
        if FakeVisionClient.error is not None:
            raise FakeVisionClient.error
        return SimpleNamespace(content=FakeVisionClient.reply)

    async def close(self) -> None:
        """Record that the client was closed."""
        self.closed = True

    @classmethod
    def reset(cls) -> None:
        """Clear recorded instances and scripted behaviour."""
        cls.instances = []
        cls.reply = "A red square."
        cls.error = None


class FakeApp:
    """The slice of ``ConsensusApp`` the image tools touch."""

    def __init__(self, db, moderator_id: int | None = None) -> None:
        self.db = db
        self.discussion = (SimpleNamespace(moderator_id=moderator_id)
                           if moderator_id is not None else None)
        self.key_requests: list[tuple[int, str]] = []

    def _resolve_key_for_moderator(self, provider_id: int,
                                   env_var: str) -> str:
        """Record the lookup and return a key naming the provider."""
        self.key_requests.append((provider_id, env_var))
        return f"key-{provider_id}"
