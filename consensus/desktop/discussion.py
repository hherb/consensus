"""Bridge methods for a discussion: setup, turn lifecycle,
pause/resume/reopen, and history."""


class DiscussionBridgeMixin:
    """Discussion setup, turn lifecycle and history."""

    # -- Discussion setup --
    def add_to_discussion(self, entity_id: int, is_moderator: bool = False,
                          also_participant: bool = False,
                          participant_role: str = "standard") -> dict:
        """Add a saved entity to the current discussion."""
        return self.app.add_to_discussion(
            entity_id, is_moderator, also_participant, participant_role,
        )

    def remove_from_discussion(self, entity_id: int) -> bool:
        """Remove an entity from the discussion."""
        return self.app.remove_from_discussion(entity_id)

    def set_moderator(self, entity_id: int,
                      also_participant: bool = False) -> bool:
        """Designate a moderator for the discussion."""
        return self.app.set_moderator(entity_id, also_participant)

    def set_participant_role(self, entity_id: int,
                             participant_role: str = "standard") -> dict:
        """Set or change a participant's role."""
        return self.app.set_participant_role(entity_id, participant_role)

    def set_topic(self, topic: str) -> bool:
        """Set the discussion topic."""
        return self.app.set_topic(topic)

    def set_discussion_method(self, method_name: str) -> dict:
        """Set the discussion method (e.g. 'ach', 'belief_diffusion')."""
        return self.app.set_discussion_method(method_name)

    def list_discussion_methods(self) -> list[dict]:
        """Return metadata for all available discussion methods."""
        return self.app.list_discussion_methods()

    def recommend_method(self, topic: str, answer_type: str) -> dict:
        """Get LLM-based method recommendations."""
        return self._run_async(self.app.recommend_method(topic, answer_type))

    # -- Discussion lifecycle --
    def start_discussion(self, moderator_participates: bool = False,
                         max_rounds: int = 0,
                         cost_limit: float = 0.0) -> dict:
        """Start the discussion."""
        return self._run_async(self.app.start_discussion(
            moderator_participates, max_rounds, cost_limit))

    def set_cost_limit(self, cost_limit: float) -> dict:
        """Update the cost limit for the current discussion."""
        return self.app.set_cost_limit(cost_limit)

    def set_default_context_strategy(self, strategy: str,
                                     window_size: int = 20) -> dict:
        """Set the discussion-level default context strategy."""
        return self.app.set_default_context_strategy(strategy, int(window_size))

    def set_member_context_strategy(self, entity_id: int, strategy: str,
                                    window_size: int = 20) -> dict:
        """Set a per-entity context strategy override."""
        return self.app.set_member_context_strategy(entity_id, strategy, int(window_size))

    def submit_human_message(self, entity_id: int, content: str) -> dict:
        """Submit a message from a human participant."""
        return self.app.submit_human_message(entity_id, content)

    def submit_human_structured_message(
            self, entity_id: int, payload: dict) -> dict:
        """Submit a structured payload from a human participant (#57)."""
        return self.app.submit_human_structured_message(entity_id, payload)

    def submit_moderator_message(self, content: str) -> dict:
        """Submit a message from the human moderator."""
        return self.app.submit_moderator_message(content)

    def submit_user_input(self, request_id: str, content: str) -> dict:
        """Submit user's response to an ask_user tool request."""
        return self.app.submit_user_input(request_id, content)

    def generate_ai_turn(self) -> dict:
        """Generate the current AI speaker's contribution."""
        return self._run_async(self.app.generate_ai_turn())

    def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete the current turn with optional human moderator summary."""
        return self._run_async(self.app.complete_turn(moderator_summary))

    def reassign_turn(self, entity_id: int) -> dict:
        """Reassign the current turn to another participant."""
        return self.app.reassign_turn(entity_id)

    def mediate(self, context: str = "") -> dict:
        """Have the moderator intervene to mediate."""
        return self._run_async(self.app.mediate(context))

    def conclude(self) -> dict:
        """End the discussion and generate a conclusion."""
        return self._run_async(self.app.conclude_discussion())

    def pause_discussion(self) -> dict:
        """Pause the active discussion."""
        return self.app.pause_discussion()

    def resume_discussion(self) -> dict:
        """Resume a paused discussion."""
        return self.app.resume_discussion()

    def retry_method_switch(self) -> dict:
        """Retry a Triage method handoff blocked by the tool gate."""
        return self.app.retry_method_switch()

    def reopen_discussion(self) -> dict:
        """Reopen a concluded discussion for continuation."""
        return self.app.reopen_discussion()

    def continue_discussion(self, content: str) -> dict:
        """Continue a concluded discussion with a new user contribution."""
        return self.app.continue_discussion(content)

    # -- History --
    def load_discussion(self, discussion_id: int) -> dict:
        """Load a past discussion for review."""
        return self.app.load_discussion(discussion_id)

    def delete_discussions(self, discussion_ids: list) -> dict:
        """Soft-delete discussions by IDs."""
        return self.app.delete_discussions([int(i) for i in discussion_ids])

    def restore_discussion(self, discussion_id: int) -> dict:
        """Restore a soft-deleted discussion."""
        return self.app.restore_discussion(int(discussion_id))

    def reset(self) -> bool:
        """Reset to a clean state."""
        return self.app.reset()
