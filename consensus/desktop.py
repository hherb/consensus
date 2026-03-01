"""Desktop launcher using pywebview."""

import asyncio
import json
import os
import threading

import webview

from .app import ConsensusApp


class DesktopBridge:
    """JS-Python bridge exposed to pywebview as js_api."""

    def __init__(self, app: ConsensusApp):
        self.app = app
        self._window: webview.Window | None = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        app.set_update_callback(self._push_state)

    def _push_state(self, state):
        if self._window:
            try:
                self._window.evaluate_js(
                    f'if(typeof onStateUpdate==="function")onStateUpdate({json.dumps(state)})'
                )
            except Exception:
                pass

    def _run_async(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=120)

    # --- API methods called from JavaScript ---

    def get_state(self):
        return self.app.get_state()

    def add_entity(self, params):
        return self.app.add_entity(**params)

    def remove_entity(self, entity_id):
        return self.app.remove_entity(entity_id)

    def set_moderator(self, entity_id):
        return self.app.set_moderator(entity_id)

    def set_topic(self, topic):
        return self.app.set_topic(topic)

    def start_discussion(self):
        return self.app.start_discussion()

    def submit_human_message(self, entity_id, content):
        return self.app.submit_human_message(entity_id, content)

    def submit_moderator_message(self, content):
        return self.app.submit_moderator_message(content)

    def generate_ai_turn(self):
        return self._run_async(self.app.generate_ai_turn())

    def complete_turn(self, moderator_summary=""):
        return self._run_async(self.app.complete_turn(moderator_summary))

    def reassign_turn(self, entity_id):
        return self.app.reassign_turn(entity_id)

    def mediate(self, context=""):
        return self._run_async(self.app.mediate(context))

    def conclude(self):
        return self._run_async(self.app.conclude_discussion())

    def reset(self):
        return self.app.reset()


def launch_desktop(debug: bool = False):
    app = ConsensusApp()
    bridge = DesktopBridge(app)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    html_path = os.path.join(static_dir, "index.html")

    window = webview.create_window(
        "Consensus - Discussion Moderator",
        html_path,
        js_api=bridge,
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    bridge._window = window

    webview.start(debug=debug)
