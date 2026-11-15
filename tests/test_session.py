"""Tests for consensus.session — session validation, TTL, limits."""

import asyncio
import time

import pytest

from consensus.session import SessionManager, _SESSION_ID_RE


# --- Session ID validation ---

class TestSessionIdValidation:
    def test_valid_ids(self):
        assert SessionManager.is_valid_session_id("a" * 20) is True
        assert SessionManager.is_valid_session_id("A1_-" * 8) is True  # 32 chars
        assert SessionManager.is_valid_session_id("x" * 64) is True

    def test_too_short(self):
        assert SessionManager.is_valid_session_id("short") is False

    def test_too_long(self):
        assert SessionManager.is_valid_session_id("x" * 65) is False

    def test_invalid_characters(self):
        assert SessionManager.is_valid_session_id("a" * 19 + "!") is False
        assert SessionManager.is_valid_session_id("../" + "a" * 20) is False
        assert SessionManager.is_valid_session_id("a" * 19 + " ") is False

    def test_path_traversal_rejected(self):
        assert SessionManager.is_valid_session_id("../../etc/passwd" + "a" * 10) is False
        assert SessionManager.is_valid_session_id("." * 20) is False

    def test_empty_string(self):
        assert SessionManager.is_valid_session_id("") is False


# --- Session lifecycle ---

class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_get_app_creates_new_session(self, tmp_path):
        mgr = SessionManager(data_dir=str(tmp_path / "sessions"))
        sid = "a" * 32
        app = await mgr.get_app(sid)
        assert app is not None
        assert mgr.active_count == 1

    @pytest.mark.asyncio
    async def test_get_app_returns_same_instance(self, tmp_path):
        mgr = SessionManager(data_dir=str(tmp_path / "sessions"))
        sid = "b" * 32
        app1 = await mgr.get_app(sid)
        app2 = await mgr.get_app(sid)
        assert app1 is app2

    @pytest.mark.asyncio
    async def test_invalid_session_id_returns_none(self, tmp_path):
        mgr = SessionManager(data_dir=str(tmp_path / "sessions"))
        assert await mgr.get_app("bad!") is None
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_max_sessions_enforced(self, tmp_path):
        mgr = SessionManager(max_sessions=2, data_dir=str(tmp_path / "sessions"))
        await mgr.get_app("a" * 32)
        await mgr.get_app("b" * 32)
        result = await mgr.get_app("c" * 32)
        assert result is None
        assert mgr.active_count == 2

    @pytest.mark.asyncio
    async def test_expired_sessions_evicted_to_make_room(self, tmp_path):
        mgr = SessionManager(max_sessions=1, session_ttl=0,
                             data_dir=str(tmp_path / "sessions"))
        await mgr.get_app("a" * 32)
        # Force expiry by setting last_access to the past
        mgr._sessions["a" * 32]["last_access"] = 0
        # Now requesting a new session should evict the expired one
        result = await mgr.get_app("b" * 32)
        assert result is not None
        assert mgr.active_count == 1
        assert "a" * 32 not in mgr._sessions

    @pytest.mark.asyncio
    async def test_stop_clears_all_sessions(self, tmp_path):
        mgr = SessionManager(data_dir=str(tmp_path / "sessions"))
        await mgr.get_app("a" * 32)
        await mgr.get_app("b" * 32)
        await mgr.stop()
        assert mgr.active_count == 0


# --- Closing sessions ---

class TestSessionClose:
    """Expiry and stop() both release a session's MCP providers (#61 net)."""

    @staticmethod
    def _record_shutdowns(monkeypatch, fail: bool = False) -> list:
        from consensus.app import ConsensusApp
        calls: list = []

        async def shutdown(self):
            calls.append(self)
            if fail:
                raise RuntimeError("mcp subprocess hung")
        monkeypatch.setattr(ConsensusApp, "shutdown", shutdown)
        return calls

    async def test_expiry_shuts_the_app_down(self, tmp_path, monkeypatch):
        calls = self._record_shutdowns(monkeypatch)
        mgr = SessionManager(max_sessions=1, session_ttl=0,
                             data_dir=str(tmp_path / "sessions"))
        app = await mgr.get_app("a" * 32)
        mgr._sessions["a" * 32]["last_access"] = 0
        await mgr.get_app("b" * 32)
        assert calls == [app]

    async def test_stop_logs_a_failed_shutdown(self, tmp_path, monkeypatch,
                                               caplog):
        self._record_shutdowns(monkeypatch, fail=True)
        mgr = SessionManager(data_dir=str(tmp_path / "sessions"))
        await mgr.get_app("a" * 32)
        await mgr.stop()
        assert mgr.active_count == 0
        assert "mcp subprocess hung" in caplog.text
        assert any(r.levelname == "WARNING" for r in caplog.records)

    async def test_expiry_logs_a_failed_shutdown_and_still_evicts(
            self, tmp_path, monkeypatch, caplog):
        self._record_shutdowns(monkeypatch, fail=True)
        mgr = SessionManager(max_sessions=1, session_ttl=0,
                             data_dir=str(tmp_path / "sessions"))
        await mgr.get_app("a" * 32)
        mgr._sessions["a" * 32]["last_access"] = 0
        assert await mgr.get_app("b" * 32) is not None
        assert "a" * 32 not in mgr._sessions
        assert "mcp subprocess hung" in caplog.text
