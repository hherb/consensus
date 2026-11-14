"""Desktop window geometry and the async-bridge timeout."""

from .. import __version__

# Desktop window configuration
WINDOW_TITLE = f"Consensus v{__version__}"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 900
WINDOW_MIN_HEIGHT = 600

# Timeout for async operations bridged from pywebview (seconds)
ASYNC_BRIDGE_TIMEOUT = 180
