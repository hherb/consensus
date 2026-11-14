"""Behaviour of the desktop bridge beyond plain forwarding (issue #61 net).

Pushes to the webview, the memory/document availability gates, the native
file dialogs, the evaluation server and ``launch_desktop``. A real
``ConsensusApp`` on a temporary database; only the pywebview window and
module (which need a GUI) are faked.
"""

import json
import logging
import os
import sys
import types
import urllib.request

import pytest

from consensus import desktop
from consensus.desktop import DesktopBridge, launch_desktop
from tests.desktop_helpers import FakeWindow, bridge, stop_loop  # noqa: F401
from tests.document_helpers import patch_where_defined

UNAVAILABLE = "Memory tools failed to load; see the application log for the cause"


# ---------------------------------------------------------------------------
# Pushes to the webview
# ---------------------------------------------------------------------------

class TestPushes:
    def test_state_update_reaches_the_window(self, bridge):
        bridge._window = FakeWindow()
        bridge.app._notify()
        [script] = bridge._window.scripts
        prefix = 'if(typeof onStateUpdate==="function")onStateUpdate('
        assert script.startswith(prefix) and script.endswith(")")
        assert json.loads(script[len(prefix):-1]) == json.loads(
            json.dumps(bridge.app.get_state()))

    @pytest.mark.parametrize("event,handler", [
        ("tool_progress", "onToolProgress"),
        ("user_input_request", "onUserInputRequest"),
    ])
    def test_events_reach_the_window(self, bridge, event, handler):
        bridge._window = FakeWindow()
        bridge.app.emit(event, {"k": "v"})
        assert bridge._window.scripts == [
            f'if(window.{handler}) {handler}({{"k": "v"}})']

    @pytest.mark.parametrize("push", ["_push_state", "_push_progress",
                                      "_push_user_input_request"])
    def test_no_window_is_a_no_op(self, bridge, push):
        getattr(bridge, push)({"k": "v"})

    @pytest.mark.parametrize("push", ["_push_state", "_push_progress",
                                      "_push_user_input_request"])
    def test_a_failing_window_is_logged_not_raised(self, bridge, push,
                                                   caplog):
        bridge._window = FakeWindow(fail_js=True)
        with caplog.at_level(logging.DEBUG, logger="consensus.desktop"):
            getattr(bridge, push)({"k": "v"})
        assert any("Failed to push" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Memory configuration
# ---------------------------------------------------------------------------

class TestMemoryConfig:
    def test_unavailable(self, bridge):
        bridge.app.memory_available = False
        assert bridge.get_memory_config() == {"error": UNAVAILABLE}
        assert bridge.save_memory_config({"embedding_model": "m"}) == {
            "error": UNAVAILABLE}
        assert bridge.test_memory_connection() == {
            "ok": False, "message": UNAVAILABLE}

    def test_save_keeps_only_known_keys(self, bridge):
        bridge.app.memory_available = True
        assert bridge.save_memory_config(
            {"embedding_model": "m", "embedding_endpoint": 7,
             "evil": "x"}) == {"ok": True}
        config = bridge.get_memory_config()
        assert config["embedding_model"] == "m"
        assert config["embedding_endpoint"] == "7"
        assert "evil" not in config

    def test_connection_result(self, bridge, monkeypatch):
        import consensus.tools_memory as tm
        bridge.app.memory_available = True

        async def ok(self):
            return True, "fine"

        monkeypatch.setattr(tm.EmbeddingClient, "test_connection", ok)
        assert bridge.test_memory_connection() == {"ok": True,
                                                   "message": "fine"}

    def test_connection_failure_is_reported_and_logged(self, bridge,
                                                       monkeypatch, caplog):
        import consensus.tools_memory as tm
        bridge.app.memory_available = True

        async def boom(self):
            raise RuntimeError("no ollama")

        monkeypatch.setattr(tm.EmbeddingClient, "test_connection", boom)
        with caplog.at_level(logging.ERROR, logger="consensus.desktop"):
            assert bridge.test_memory_connection() == {
                "ok": False, "message": "no ollama"}
        assert caplog.records[-1].exc_info is not None


# ---------------------------------------------------------------------------
# Native file dialogs
# ---------------------------------------------------------------------------

def _record_async(monkeypatch, app, name):
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        return {"ok": name}

    monkeypatch.setattr(app, name, fake)
    return calls


class TestUploads:
    @pytest.mark.parametrize("method", ["upload_document", "upload_image"])
    def test_no_window(self, bridge, method):
        bridge.app.documents_available = True
        assert getattr(bridge, method)() == {"error": "No window available"}

    def test_document_tools_unavailable(self, bridge):
        bridge._window = FakeWindow()
        bridge.app.documents_available = False
        assert bridge.upload_document() == {
            "error": "Document tools failed to load; "
                     "see the application log for the cause"}
        assert bridge.add_document_from_url("http://x") == {
            "error": "Document tools failed to load; "
                     "see the application log for the cause"}
        assert bridge._window.dialogs == []

    @pytest.mark.parametrize("method", ["upload_document", "upload_image"])
    @pytest.mark.parametrize("result", [None, [], ""])
    def test_cancelled(self, bridge, method, result):
        bridge.app.documents_available = True
        bridge._window = FakeWindow(dialog_result=result)
        assert getattr(bridge, method)() == {"cancelled": True}

    @pytest.mark.parametrize("as_list", [False, True])
    def test_document_is_read_and_added(self, bridge, monkeypatch, tmp_path,
                                        as_list):
        import webview
        path = tmp_path / "notes.md"
        path.write_bytes(b"# hi")
        bridge.app.documents_available = True
        bridge._window = FakeWindow(
            dialog_result=[str(path)] if as_list else str(path))
        calls = _record_async(monkeypatch, bridge.app, "add_document")
        assert bridge.upload_document(4) == {"ok": "add_document"}
        assert calls == [{"filename": "notes.md", "content_bytes": b"# hi",
                          "mime_type": "text/markdown", "discussion_id": 4}]
        [(kind, kwargs)] = bridge._window.dialogs
        assert kind == webview.FileDialog.OPEN
        assert kwargs["file_types"][0].startswith("Documents (")

    @pytest.mark.parametrize("method,target,fallback", [
        ("upload_document", "add_document", "application/octet-stream"),
        ("upload_image", "add_image", "image/png"),
    ])
    def test_unknown_type_falls_back(self, bridge, monkeypatch, tmp_path,
                                     method, target, fallback):
        path = tmp_path / "blob.zzunknown"
        path.write_bytes(b"x")
        bridge.app.documents_available = True
        bridge._window = FakeWindow(dialog_result=str(path))
        calls = _record_async(monkeypatch, bridge.app, target)
        getattr(bridge, method)()
        assert calls[0]["mime_type"] == fallback

    def test_image_is_read_and_added(self, bridge, monkeypatch, tmp_path):
        path = tmp_path / "pic.jpg"
        path.write_bytes(b"\xff\xd8")
        bridge._window = FakeWindow(dialog_result=(str(path),))
        calls = _record_async(monkeypatch, bridge.app, "add_image")
        assert bridge.upload_image() == {"ok": "add_image"}
        assert calls == [{"filename": "pic.jpg", "content_bytes": b"\xff\xd8",
                          "mime_type": "image/jpeg", "discussion_id": 0}]
        assert bridge._window.dialogs[0][1]["file_types"][0].startswith(
            "Images (")

    def test_document_from_url(self, bridge, monkeypatch):
        bridge.app.documents_available = True
        calls = _record_async(monkeypatch, bridge.app, "add_document_from_url")
        assert bridge.add_document_from_url("http://x", 2, "T") == {
            "ok": "add_document_from_url"}
        assert calls == [{"url": "http://x", "discussion_id": 2, "title": "T"}]


class TestSaveFile:
    def test_no_window(self, bridge):
        assert bridge.save_file("c", "f.md") is False

    def test_cancelled(self, bridge):
        bridge._window = FakeWindow(dialog_result=None)
        assert bridge.save_file("c", "f.md") is False

    @pytest.mark.parametrize("as_list", [False, True])
    def test_writes_utf8(self, bridge, tmp_path, as_list):
        import webview
        out = tmp_path / "out.md"
        bridge._window = FakeWindow(
            dialog_result=[str(out)] if as_list else str(out))
        assert bridge.save_file("é", "f.md", "Markdown (*.md)") is True
        assert out.read_text(encoding="utf-8") == "é"
        assert bridge._window.dialogs == [(webview.FileDialog.SAVE, {
            "save_filename": "f.md", "file_types": ("Markdown (*.md)",)})]

    def test_default_filter(self, bridge, tmp_path):
        bridge._window = FakeWindow(dialog_result=str(tmp_path / "o.txt"))
        bridge.save_file("c", "o.txt")
        assert bridge._window.dialogs[0][1]["file_types"] == (
            "All files (*.*)",)


# ---------------------------------------------------------------------------
# Evaluation server
# ---------------------------------------------------------------------------

class TestOpenEvaluation:
    def test_starts_once_and_serves(self, bridge, monkeypatch, tmp_path):
        import webbrowser

        from consensus.evaluation import eval_db
        real = eval_db.EvalDatabase
        monkeypatch.setattr(eval_db, "EvalDatabase",
                            lambda: real(str(tmp_path / "eval.db")))
        opened = []
        monkeypatch.setattr(webbrowser, "open", opened.append)
        try:
            first = bridge.open_evaluation()
            url = first["url"]
            assert url.startswith("http://127.0.0.1:") and url.endswith(
                "/eval/")
            with urllib.request.urlopen(url, timeout=5) as resp:
                assert resp.status == 200
            assert bridge.open_evaluation() == first
            assert opened == [url, url]
        finally:
            if hasattr(bridge, "_eval_runner"):
                bridge._run_async(bridge._eval_runner.cleanup())

    def test_failure_is_reported_and_logged(self, bridge, monkeypatch,
                                            caplog):
        from consensus.evaluation import eval_db

        def broken():
            raise OSError("disk full")

        monkeypatch.setattr(eval_db, "EvalDatabase", broken)
        with caplog.at_level(logging.ERROR, logger="consensus.desktop"):
            assert bridge.open_evaluation() == {
                "error": "Evaluation module not available"}
        assert caplog.records[-1].exc_info is not None


# ---------------------------------------------------------------------------
# launch_desktop
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_webview(monkeypatch, app):
    """A pywebview stand-in that records the window and start arguments."""
    mod = types.SimpleNamespace(created=[], started=[])
    window = FakeWindow()

    def create_window(*args, **kwargs):
        mod.created.append((args, kwargs))
        return window

    mod.create_window = create_window
    mod.start = lambda **kwargs: mod.started.append(kwargs)
    mod.window = window
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setattr("consensus.config.load_env", lambda: None)
    patch_where_defined(monkeypatch, launch_desktop, "ConsensusApp",
                        lambda: app)
    yield mod
    for args, kwargs in mod.created:
        stop_loop(kwargs["js_api"])


class TestLaunchDesktop:
    def test_opens_the_frontend_with_the_bridge(self, fake_webview, app):
        launch_desktop(debug=True)
        [(args, kwargs)] = fake_webview.created
        title, html_path = args
        assert title == desktop.WINDOW_TITLE
        assert os.path.isfile(html_path)
        assert html_path.endswith(os.path.join("static", "index.html"))
        bridge = kwargs["js_api"]
        assert isinstance(bridge, DesktopBridge) and bridge.app is app
        assert bridge._window is fake_webview.window
        assert kwargs["width"] == desktop.WINDOW_WIDTH
        assert kwargs["height"] == desktop.WINDOW_HEIGHT
        assert kwargs["min_size"] == (desktop.WINDOW_MIN_WIDTH,
                                      desktop.WINDOW_MIN_HEIGHT)
        assert kwargs["text_select"] is True
        assert fake_webview.started[-1]["debug"] is True

    def test_linux_passes_the_icon_and_disables_dmabuf(
            self, fake_webview, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("WEBKIT_DISABLE_DMABUF_RENDERER", raising=False)
        launch_desktop()
        assert os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] == "1"
        started = fake_webview.started[-1]
        assert started["debug"] is False
        assert os.path.isfile(started["icon"])
        assert os.path.basename(started["icon"]) == "consensus_icon.png"

    def test_other_platforms_get_no_icon_kwarg(self, fake_webview,
                                               monkeypatch):
        monkeypatch.setattr(sys, "platform", "sunos5")
        launch_desktop()
        assert "icon" not in fake_webview.started[-1]

    def test_macos_sets_the_dock_icon(self, fake_webview, monkeypatch):
        icons = []
        ns_app = types.SimpleNamespace(setApplicationIconImage_=icons.append)
        image = types.SimpleNamespace(initWithContentsOfFile_=lambda p: p)
        appkit = types.SimpleNamespace(
            NSApplication=types.SimpleNamespace(
                sharedApplication=lambda: ns_app),
            NSImage=types.SimpleNamespace(alloc=lambda: image))
        monkeypatch.setitem(sys.modules, "AppKit", appkit)
        monkeypatch.setattr(sys, "platform", "darwin")
        launch_desktop()
        [icon] = icons
        assert os.path.basename(icon) == "consensus_icon.png"

    def test_macos_without_appkit_still_launches(self, fake_webview,
                                                 monkeypatch, caplog):
        monkeypatch.setitem(sys.modules, "AppKit", None)
        monkeypatch.setattr(sys, "platform", "darwin")
        with caplog.at_level(logging.DEBUG, logger="consensus.desktop"):
            launch_desktop()
        assert fake_webview.started
        assert any("dock icon" in r.getMessage() for r in caplog.records)

    def test_windows_sets_the_app_id(self, fake_webview, monkeypatch):
        import ctypes
        ids = []
        shell32 = types.SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=ids.append)
        monkeypatch.setattr(ctypes, "windll",
                            types.SimpleNamespace(shell32=shell32),
                            raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        launch_desktop()
        assert ids == ["consensus.discussion.moderator"]

    def test_missing_icon_is_skipped(self, fake_webview, monkeypatch):
        real_exists = os.path.exists
        monkeypatch.setattr(os.path, "exists", lambda p: (
            False if p.endswith("consensus_icon.png") else real_exists(p)))
        monkeypatch.setattr(sys, "platform", "linux")
        launch_desktop()
        assert "icon" not in fake_webview.started[-1]


def test_image_data_url_of_unknown_image(bridge):
    assert bridge.get_image_data_url(999) == ""
