"""Route tests for ``--multi-user`` sessions on the file routes, and SSE.

Only ``POST /api/<method>`` may create a session (#132); the SSE stream
ends once its session is evicted, so the browser rebinds (#135).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from consensus import server
from consensus.app import ConsensusApp
from consensus.server import routes_misc
from consensus.server.constants import NO_SESSION_ERROR
from tests.server_helpers import (  # noqa: F401  (fixtures)
    api, fast_hashing, module_of, multi_client, server_home, signed_in,
    single_client,
)

# Every /api/ route outside the dispatch table: none may create a session.
NO_SESSION_ROUTES = [
    ("get", "/api/memory/config"), ("put", "/api/memory/config"),
    ("post", "/api/memory/test"), ("post", "/api/documents/upload"),
    ("post", "/api/documents/add-url"), ("get", "/api/documents/1"),
    ("delete", "/api/documents/1"), ("delete", "/api/documents/1/2"),
    ("post", "/api/images/upload"), ("post", "/api/images/add-url"),
    ("get", "/api/images/1"), ("get", "/api/images/file/1"),
    ("delete", "/api/images/1"), ("delete", "/api/images/1/2"),
    ("get", "/api/events"),
]


class TestMultiUserFileRoutes:
    """The file routes sit behind the same authentication gate."""

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/memory/config"), ("put", "/api/memory/config"),
        ("post", "/api/memory/test"), ("post", "/api/documents/add-url"),
        ("get", "/api/documents/1"), ("delete", "/api/documents/1"),
        ("delete", "/api/documents/1/2"), ("post", "/api/images/add-url"),
        ("get", "/api/images/1"), ("get", "/api/images/file/1"),
        ("delete", "/api/images/1"), ("delete", "/api/images/1/2"),
        ("get", "/api/events"), ("post", "/api/documents/upload"),
        ("post", "/api/images/upload"),
    ])
    async def test_unauthenticated_is_a_401(self, multi_client, method, path):
        resp = await getattr(multi_client, method)(path, json={})
        assert resp.status == 401

    @pytest.mark.parametrize("method,path", NO_SESSION_ROUTES)
    async def test_without_a_session_is_a_400(self, signed_in, method, path):
        """Only ``POST /api/<method>`` creates a session (#132).

        These routes cannot set the cookie, so a session they created would
        be orphaned, holding capacity until its TTL.
        """
        resp = await getattr(signed_in, method)(path, json={})
        assert resp.status == 400, path
        assert (await resp.json()) == {"error": NO_SESSION_ERROR}
        assert signed_in.app[server.CTX_KEY].session_mgr.active_count == 0

    async def test_an_evicted_session_is_a_400(self, signed_in):
        await api(signed_in, "get_state")
        mgr = signed_in.app[server.CTX_KEY].session_mgr
        for info in mgr._sessions.values():
            info["last_access"] = 0
        await mgr._close_apps(mgr._evict_expired())
        resp = await signed_in.get("/api/documents/1")
        assert resp.status == 400
        assert mgr.active_count == 0

    async def test_the_session_a_dispatch_call_made_is_used(self, signed_in):
        await api(signed_in, "get_state")
        resp = await signed_in.get("/api/documents/1")
        assert resp.status == 200
        assert signed_in.app[server.CTX_KEY].session_mgr.active_count == 1


class TestEvents:
    async def _read_event(self, resp) -> tuple[str, dict]:
        lines = []
        while True:
            line = (await asyncio.wait_for(resp.content.readline(), 5)).decode()
            if line == "\n" and lines:
                break
            if line.strip():
                lines.append(line.strip())
        fields = dict(line.split(": ", 1) for line in lines)
        return fields["event"], json.loads(fields["data"])

    async def test_streams_app_events(self, single_client, monkeypatch):
        listeners: dict = {}
        real_on = ConsensusApp.on

        def spy_on(self, name, callback):
            listeners[name] = (self, callback)
            return real_on(self, name, callback)
        monkeypatch.setattr(ConsensusApp, "on", spy_on)

        resp = await single_client.get("/api/events")
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"
        await asyncio.sleep(0)
        app, _ = listeners["tool_progress"]
        app.emit("tool_progress", {"stage": "searching"})
        assert await self._read_event(resp) == ("tool_progress",
                                                {"stage": "searching"})
        app.emit("user_input_request", {"request_id": "r1"})
        assert await self._read_event(resp) == ("user_input_request",
                                                {"request_id": "r1"})
        resp.close()

    async def test_keepalive_when_idle(self, single_client, monkeypatch):
        monkeypatch.setattr(module_of(routes_misc.handle_events),
                            "SSE_KEEPALIVE_SECONDS", 0.01)
        resp = await single_client.get("/api/events")
        line = await asyncio.wait_for(resp.content.readline(), 5)
        assert line == b": keepalive\n"
        resp.close()

    async def test_stream_ends_when_its_session_is_evicted(self, signed_in,
                                                          monkeypatch):
        """A stream bound to an evicted app ends, so EventSource rebinds (#135).

        Otherwise the session's next app never reaches the browser: its
        ask_user bubble would hang until the timeout.
        """
        monkeypatch.setattr(module_of(routes_misc.handle_events),
                            "SSE_KEEPALIVE_SECONDS", 0.01)
        await api(signed_in, "get_state")
        resp = await signed_in.get("/api/events")
        assert resp.status == 200
        assert await asyncio.wait_for(resp.content.readline(), 5) == (
            b": keepalive\n")
        mgr = signed_in.app[server.CTX_KEY].session_mgr
        for info in mgr._sessions.values():
            info["last_access"] = 0
        await mgr._close_apps(mgr._evict_expired())
        await asyncio.wait_for(resp.content.read(), 5)  # EOF, not a hang
        assert resp.content.at_eof()

    async def test_stream_survives_while_its_session_lives(self, signed_in,
                                                           monkeypatch):
        """Keepalives continue, and do not count as session activity."""
        monkeypatch.setattr(module_of(routes_misc.handle_events),
                            "SSE_KEEPALIVE_SECONDS", 0.01)
        await api(signed_in, "get_state")
        resp = await signed_in.get("/api/events")
        mgr = signed_in.app[server.CTX_KEY].session_mgr
        (info,) = mgr._sessions.values()
        info["last_access"] = 1.0  # stale but not yet evicted
        lines = [await asyncio.wait_for(resp.content.readline(), 5)
                 for _ in range(6)]
        assert lines == [b": keepalive\n", b"\n"] * 3
        assert info["last_access"] == 1.0, "an open tab extended the TTL"
        resp.close()
