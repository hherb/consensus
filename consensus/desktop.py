"""Desktop launcher using pywebview with JS-Python bridge."""

import asyncio
import json
import os
import threading

from .app import ConsensusApp


class DesktopBridge:
    """API exposed to JavaScript via pywebview's js_api."""

    def __init__(self, app: ConsensusApp):
        self.app = app
        self._window = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        app.set_update_callback(self._push_state)

    def _push_state(self, state):
        if self._window:
            try:
                js = (
                    'if(typeof onStateUpdate==="function")'
                    f'onStateUpdate({json.dumps(state)})'
                )
                self._window.evaluate_js(js)
            except Exception:
                pass

    def _run_async(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=180)

    # -- State --
    def get_state(self):
        return self.app.get_state()

    # -- Providers --
    def add_provider(self, name, base_url, api_key_env=""):
        return self.app.add_provider(name, base_url, api_key_env)

    def update_provider(self, provider_id, name="", base_url="",
                        api_key_env=""):
        kwargs = {}
        if name:
            kwargs["name"] = name
        if base_url:
            kwargs["base_url"] = base_url
        if api_key_env is not None:
            kwargs["api_key_env"] = api_key_env
        return self.app.update_provider(provider_id, **kwargs)

    def delete_provider(self, provider_id):
        return self.app.delete_provider(provider_id)

    # -- Entity profiles --
    def save_entity(self, name, entity_type, avatar_color="#3b82f6",
                    provider_id="", model="", temperature=0.7,
                    max_tokens=1024, system_prompt="", entity_id=""):
        return self.app.save_entity(
            name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt, entity_id,
        )

    def delete_entity(self, entity_id):
        return self.app.delete_entity(entity_id)

    # -- Prompts --
    def save_prompt(self, prompt_id, name, role, target, task, content):
        return self.app.save_prompt(
            prompt_id, name, role, target, task, content,
        )

    def delete_prompt(self, prompt_id):
        return self.app.delete_prompt(prompt_id)

    # -- Discussion setup --
    def add_to_discussion(self, entity_id, is_moderator=False,
                          also_participant=False):
        return self.app.add_to_discussion(
            entity_id, is_moderator, also_participant,
        )

    def remove_from_discussion(self, entity_id):
        return self.app.remove_from_discussion(entity_id)

    def set_moderator(self, entity_id, also_participant=False):
        return self.app.set_moderator(entity_id, also_participant)

    def set_topic(self, topic):
        return self.app.set_topic(topic)

    # -- Discussion lifecycle --
    def start_discussion(self, moderator_participates=False):
        return self.app.start_discussion(moderator_participates)

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

    # -- History --
    def load_discussion(self, discussion_id):
        return self.app.load_discussion(discussion_id)

    def reset(self):
        return self.app.reset()


def launch_desktop(debug: bool = False):
    import webview

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
