"""ConsensusApp mixin: Discussion history, pause/resume/reopen, ask_user input and reset.

Moved verbatim from ``consensus/app.py`` (issue #61). The host class
is ``ConsensusApp``, which owns all state.
"""

from .. import app_discussion_flow, app_discussion_state


class HistoryMixin:
    """Discussion history, pause/resume/reopen, ask_user input and reset.

    Expects the host class to provide: db, discussion, moderator,
    tool_registry, _pending_user_inputs, get_state(), _notify(),
    _resolve_key_for_moderator(), _emit_tool_progress().
    """

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export."""
        return app_discussion_state.get_export_data(self.db, discussion_id)

    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion."""
        result = app_discussion_state.load_discussion(
            self.db, discussion_id,
            self._resolve_key_for_moderator, self.tool_registry,
            progress_callback=self._emit_tool_progress,
        )
        if isinstance(result, dict):
            return result  # error
        self.discussion, self.moderator = result
        self._notify()
        return self.get_state()

    def delete_discussions(self, discussion_ids: list[int]) -> dict:
        """Soft-delete discussions."""
        result = app_discussion_state.delete_discussions(self.db, discussion_ids)
        result["state"] = self.get_state()
        return result

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        result = app_discussion_state.restore_discussion(self.db, discussion_id)
        result["state"] = self.get_state()
        return result

    def pause_discussion(self) -> dict:
        """Pause the current active discussion."""
        self._cancel_pending_user_inputs()
        result = app_discussion_state.pause_discussion(self.discussion, self.db)
        if "error" in result:
            return result
        self._notify()
        return self.get_state()

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        result = app_discussion_state.resume_discussion(self.discussion, self.db)
        if "error" in result:
            return result
        self._notify()
        return self.get_state()

    def retry_method_switch(self) -> dict:
        """Retry a Triage method handoff blocked by the tool gate."""
        result = app_discussion_flow.retry_method_switch(
            self.discussion, self.db, self.get_state,
        )
        self._notify()
        return result

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion."""
        result = app_discussion_state.reopen_discussion(self.discussion, self.db)
        if "error" in result:
            return result
        self._notify()
        return self.get_state()

    def continue_discussion(self, content: str) -> dict:
        """Continue a concluded discussion with a new user contribution."""
        result = app_discussion_state.continue_discussion(
            self.discussion, self.db, content,
        )
        if "error" in result:
            return result
        self._notify()
        return self.get_state()

    # ------------------------------------------------------------------
    # Interactive user input (ask_user tool)
    # ------------------------------------------------------------------

    def submit_user_input(self, request_id: str, content: str) -> dict:
        """Resolve a pending ask_user request with the user's response."""
        entry = self._pending_user_inputs.get(request_id)
        if not entry:
            return {"error": "No pending input request with that ID"}
        future, _data = entry
        if future.done():
            return {"error": "Request already resolved"}
        future.set_result(content)
        return {"ok": True}

    def cancel_user_input(self, request_id: str) -> dict:
        """Cancel a pending ask_user request."""
        entry = self._pending_user_inputs.pop(request_id, None)
        if not entry:
            return {"error": "No pending input request with that ID"}
        future, _data = entry
        if not future.done():
            future.cancel()
        return {"ok": True}

    def _cancel_pending_user_inputs(self) -> None:
        """Cancel all pending user-input futures (e.g. on pause/conclude)."""
        for request_id, (future, _data) in list(self._pending_user_inputs.items()):
            if not future.done():
                future.cancel()
        self._pending_user_inputs.clear()

    def reset(self) -> bool:
        """Reset to a clean state."""
        self.discussion, self.moderator = app_discussion_state.reset_discussion(
            self.db, self._resolve_key_for_moderator, self.tool_registry,
            progress_callback=self._emit_tool_progress,
        )
        self._notify()
        return True
