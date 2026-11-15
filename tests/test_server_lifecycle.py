"""Route tests for static serving, the eval mount and the server lifecycle."""

from __future__ import annotations

import pytest
from yarl import URL

from consensus import server
from consensus.app import ConsensusApp
from tests.server_helpers import (  # noqa: F401  (fixtures)
    fast_hashing, make_client, module_of, multi_client, server_home,
    single_client,
)


class TestStatic:
    async def test_index(self, single_client):
        resp = await single_client.get("/")
        assert resp.status == 200
        assert "<html" in (await resp.text()).lower()

    async def test_existing_file(self, single_client):
        resp = await single_client.get("/api.js")
        assert resp.status == 200
        assert "getImageUrl" in await resp.text()

    async def test_unknown_path_serves_the_spa(self, single_client):
        resp = await single_client.get("/some/client/route")
        assert resp.status == 200
        assert "<html" in (await resp.text()).lower()

    @pytest.mark.parametrize("raw", ["/..%2fserver.py", "/%2e%2e/server.py",
                                     "/..%2f..%2fpyproject.toml"])
    async def test_traversal_is_forbidden(self, single_client, raw):
        resp = await single_client.get(URL(raw, encoded=True))
        assert resp.status == 403
        assert await resp.text() == "Forbidden"

    async def test_health(self, single_client):
        assert (await (await single_client.get("/health")).json()) == \
            {"status": "ok"}

    async def test_health_counts_sessions_in_multi_user(self, multi_client):
        body = await (await multi_client.get("/health")).json()
        assert body == {"status": "ok", "active_sessions": 0}


class TestEvalMount:
    async def test_mounted_in_single_user(self, single_client):
        resp = await single_client.get("/eval/")
        assert resp.status == 200
        assert "eval" in (await resp.text()).lower()

    async def test_not_mounted_in_multi_user(self, multi_client, caplog):
        resp = await multi_client.get("/eval/api/cases")
        assert resp.status == 200  # the SPA catch-all, not the eval API
        assert resp.content_type == "text/html"

    async def test_load_failure_is_logged_not_fatal(self, server_home,
                                                    monkeypatch, caplog):
        from consensus.evaluation.eval_db import EvalDatabase

        def boom(self, *a, **kw):
            raise RuntimeError("no eval db")
        monkeypatch.setattr(EvalDatabase, "__init__", boom)
        client = await make_client(multi_user=False)
        try:
            assert (await client.get("/health")).status == 200
        finally:
            await client.close()
        assert "Failed to load evaluation module" in caplog.text


class TestLifecycle:
    async def test_cleanup_shuts_the_shared_app_down(self, server_home,
                                                     monkeypatch):
        calls: list = []

        async def shutdown(self):
            calls.append(self)
        monkeypatch.setattr(ConsensusApp, "shutdown", shutdown)
        client = await make_client(multi_user=False)
        await client.close()
        assert len(calls) == 1

    async def test_multi_user_starts_and_stops_the_session_manager(
            self, server_home, fast_hashing, monkeypatch):
        from consensus.session import SessionManager
        events: list = []
        monkeypatch.setattr(SessionManager, "start_cleanup_loop",
                            lambda self: events.append("start"))

        async def stop(self):
            events.append("stop")
        monkeypatch.setattr(SessionManager, "stop", stop)
        client = await make_client(multi_user=True)
        assert events == ["start"]
        await client.close()
        assert events == ["start", "stop"]

    async def test_launch_web_serves_until_stopped(self, server_home,
                                                   monkeypatch, capsys):
        """``launch_web`` binds, announces itself, and cleans up on exit."""
        calls: list = []

        async def shutdown(self):
            calls.append("shutdown")
        monkeypatch.setattr(ConsensusApp, "shutdown", shutdown)

        class StopAtOnce:
            async def wait(self):
                calls.append("serving")

        monkeypatch.setattr(module_of(server.launch_web).asyncio, "Event",
                            StopAtOnce)
        await server.launch_web("127.0.0.1", 0)
        assert calls == ["serving", "shutdown"]
        assert "single-user mode" in capsys.readouterr().out
