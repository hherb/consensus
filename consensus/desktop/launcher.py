"""``launch_desktop``: open the pywebview window on the bridge."""

import logging
import os
import sys

from ..app import ConsensusApp
from .bridge import DesktopBridge
from .constants import (
    WINDOW_HEIGHT, WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH, WINDOW_TITLE,
    WINDOW_WIDTH,
)

logger = logging.getLogger(__name__)


def launch_desktop(debug: bool = False) -> None:
    """Launch the desktop application using pywebview."""
    # On Linux, disable WebKit DMA-BUF renderer to avoid blank windows
    # on systems where GPU/DRM access is restricted (e.g. DGX, VMs,
    # containers, or users not in the 'render'/'video' group).
    if sys.platform.startswith("linux"):
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

    import webview
    from ..config import load_env
    load_env()

    app = ConsensusApp()
    bridge = DesktopBridge(app)

    # The package directory is consensus/, one level above this file.
    pkg_dir = os.path.dirname(os.path.dirname(__file__))
    static_dir = os.path.join(pkg_dir, "static")
    html_path = os.path.join(static_dir, "index.html")

    # App icon — resolve from assets/ relative to repo root
    icon_path = os.path.normpath(
        os.path.join(pkg_dir, "..", "assets", "consensus_icon.png"))
    if not os.path.exists(icon_path):
        icon_path = None

    # macOS: set dock icon via AppKit (pywebview doesn't support this natively)
    if icon_path and sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSImage
            ns_app = NSApplication.sharedApplication()
            ns_app.setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(icon_path))
        except Exception:
            logger.debug("Could not set macOS dock icon", exc_info=True)

    # Windows: set taskbar icon via ctypes
    if icon_path and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "consensus.discussion.moderator")
        except Exception:
            logger.debug("Could not set Windows app ID", exc_info=True)

    window = webview.create_window(
        WINDOW_TITLE,
        html_path,
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
        text_select=True,
    )
    bridge._window = window

    # icon param in webview.start() works on Linux (GTK/QT)
    start_kwargs = {"debug": debug}
    if icon_path and sys.platform.startswith("linux"):
        start_kwargs["icon"] = icon_path
    webview.start(**start_kwargs)
