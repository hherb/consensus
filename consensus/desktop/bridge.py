"""``DesktopBridge``: the object pywebview exposes to JavaScript.

Its own methods are the event-loop plumbing, state pushes, image
data URLs and the evaluation server; the rest come from the
mixins, one per area of the UI."""

import asyncio
import json
import logging
import threading
from typing import Optional

from ..app import ConsensusApp
from .catalog import CatalogBridgeMixin
from .constants import ASYNC_BRIDGE_TIMEOUT
from .discussion import DiscussionBridgeMixin
from .files import FilesBridgeMixin

logger = logging.getLogger(__name__)


class DesktopBridge(CatalogBridgeMixin, DiscussionBridgeMixin,
                    FilesBridgeMixin):
    """API exposed to JavaScript via pywebview's js_api.

    Each public method (no leading underscore) is callable from the
    browser via ``window.pywebview.api.<method_name>(...)``.  Async
    application methods are bridged through a background event loop.
    """

    def __init__(self, app: ConsensusApp) -> None:
        self.app = app
        self._window: Optional[object] = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        app.set_update_callback(self._push_state)
        app.on("tool_progress", self._push_progress)
        app.on("user_input_request", self._push_user_input_request)

    def _push_state(self, state: dict) -> None:
        """Push state updates to the frontend via evaluate_js."""
        if self._window:
            try:
                js = (
                    'if(typeof onStateUpdate==="function")'
                    f'onStateUpdate({json.dumps(state)})'
                )
                self._window.evaluate_js(js)
            except Exception:
                logger.debug("Failed to push state to webview", exc_info=True)

    def _push_progress(self, data: dict) -> None:
        """Forward tool progress events to the JS frontend."""
        if self._window is None:
            return
        try:
            payload = json.dumps(data)
            self._window.evaluate_js(f"if(window.onToolProgress) onToolProgress({payload})")
        except Exception:
            logger.debug("Failed to push progress event")

    def _push_user_input_request(self, data: dict) -> None:
        """Forward ask_user input request events to the JS frontend."""
        if self._window is None:
            return
        try:
            payload = json.dumps(data)
            self._window.evaluate_js(
                f"if(window.onUserInputRequest) onUserInputRequest({payload})"
            )
        except Exception:
            logger.debug("Failed to push user input request event")

    def _run_async(self, coro: object) -> object:
        """Run an async coroutine on the background event loop and block for result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=ASYNC_BRIDGE_TIMEOUT)

    # -- State --
    def get_state(self) -> dict:
        """Return full application state."""
        return self.app.get_state()

    def get_image_data_url(self, image_id: int) -> str:
        """Return a base64 data URL for the given image ID (for desktop rendering)."""
        from ..tools_image import image_to_base64_url
        img = self.app.db.get_image(image_id)
        if not img:
            return ""
        try:
            return image_to_base64_url(img["storage_path"], img["mime_type"])
        except OSError as e:
            # Missing or unreadable file: a storage fault, not a bug.
            logger.warning("Could not load image %d for display: %s", image_id, e)
            return ""
        except Exception:
            # Path traversal in storage_path (ValueError) or a bug: never
            # expected, so keep the traceback.
            logger.exception("Could not load image %d for display", image_id)
            return ""

    # -- Evaluation --
    def open_evaluation(self) -> dict:
        """Start the evaluation server (if needed) and open in browser."""
        return self._run_async(self._open_evaluation_async())

    async def _open_evaluation_async(self) -> dict:
        if not hasattr(self, '_eval_server_url'):
            try:
                from ..evaluation.eval_db import EvalDatabase
                from ..evaluation.eval_routes import register_eval_routes
                from aiohttp import web

                eval_db = EvalDatabase()
                eval_app = web.Application()
                register_eval_routes(eval_app, eval_db)

                runner = web.AppRunner(eval_app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                # Get the actual port assigned
                port = site._server.sockets[0].getsockname()[1]
                self._eval_server_url = f"http://127.0.0.1:{port}/eval/"
                self._eval_runner = runner
                logger.info("Evaluation server started at %s",
                            self._eval_server_url)
            except Exception:
                logger.exception("Failed to start evaluation server")
                return {"error": "Evaluation module not available"}

        import webbrowser
        webbrowser.open(self._eval_server_url)
        return {"url": self._eval_server_url}
