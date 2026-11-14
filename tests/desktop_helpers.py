"""Shared fakes and fixtures for the desktop-bridge tests (issue #61 net).

The bridge runs against a real :class:`ConsensusApp` on a temporary database
(the ``app`` fixture in ``tests/conftest.py``); only the pywebview window,
which needs a GUI, is faked.
"""

import pytest

from consensus.desktop import DesktopBridge


class FakeWindow:
    """Stand-in for a pywebview window.

    Records ``evaluate_js`` scripts and file-dialog calls; the dialog returns
    ``dialog_result``. With ``fail_js`` set, ``evaluate_js`` raises, as it
    does when the window is closing.
    """

    def __init__(self, dialog_result=None, fail_js: bool = False) -> None:
        self.dialog_result = dialog_result
        self.fail_js = fail_js
        self.scripts: list[str] = []
        self.dialogs: list[tuple] = []

    def evaluate_js(self, script: str) -> None:
        if self.fail_js:
            raise RuntimeError("window closed")
        self.scripts.append(script)

    def create_file_dialog(self, dialog_type, **kwargs):
        self.dialogs.append((dialog_type, kwargs))
        return self.dialog_result


def stop_loop(bridge: DesktopBridge) -> None:
    """Stop the bridge's background event-loop thread."""
    bridge._loop.call_soon_threadsafe(bridge._loop.stop)


@pytest.fixture
def bridge(app):
    """A bridge over the temporary-database app, with no window yet."""
    b = DesktopBridge(app)
    yield b
    stop_loop(b)
