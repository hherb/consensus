"""Desktop launcher using pywebview with a JS-Python bridge.

Split into a package by concern (issue #61): ``bridge`` holds
``DesktopBridge``, composed of the ``catalog``, ``discussion`` and ``files``
mixins; ``launcher`` holds ``launch_desktop``; ``constants`` the window
geometry and bridge timeout.
"""

from .bridge import DesktopBridge
from .constants import (
    ASYNC_BRIDGE_TIMEOUT, WINDOW_HEIGHT, WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH,
    WINDOW_TITLE, WINDOW_WIDTH,
)
from .launcher import launch_desktop

__all__ = [
    "ASYNC_BRIDGE_TIMEOUT", "DesktopBridge", "WINDOW_HEIGHT",
    "WINDOW_MIN_HEIGHT", "WINDOW_MIN_WIDTH", "WINDOW_TITLE", "WINDOW_WIDTH",
    "launch_desktop",
]
