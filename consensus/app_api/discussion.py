"""ConsensusApp mixin: Discussion setup (roster, topic, method, context strategy) and the turn lifecycle.

Moved verbatim from ``consensus/app.py`` (issue #61). The host class
is ``ConsensusApp``, which owns all state.
"""

import logging

from ..models import EntityType
from .. import app_discussion_flow, app_discussion_roles, app_discussion_setup


logger = logging.getLogger(__name__)


class DiscussionMixin:
    """Discussion setup (roster, topic, method, context strategy) and the turn lifecycle.

    Expects the host class to provide: db, discussion, moderator,
    get_state(), _notify(), _resolve_key_for_moderator(),
    _cancel_pending_user_inputs().
    """

    # ------------------------------------------------------------------
    # Discussion setup
    # ------------------------------------------------------------------

    def add_to_discussion(self, entity_id: int,
                          is_moderator: bool = False,
                          also_participant: bool = False,
                          participant_role: str = "standard") -> dict:
        """Add a saved entity to the current discussion."""
        result = app_discussion_setup.add_to_discussion(
            self.discussion, self.db, entity_id,
            is_moderator, also_participant, participant_role,
        )
        self._notify()
        return result

    def remove_from_discussion(self, entity_id: int) -> dict | bool:
        """Remove an entity from the current discussion."""
        result = app_discussion_setup.remove_from_discussion(
            self.discussion, self.db, entity_id,
        )
        self._notify()
        return result

    def set_moderator(self, entity_id: int,
                      also_participant: bool = False) -> bool:
        """Designate an entity as the moderator."""
        result = app_discussion_setup.set_moderator(self.discussion, entity_id, also_participant)
        if result:
            self._notify()
        return result

    def set_topic(self, topic: str) -> bool:
        """Set the discussion topic."""
        result = app_discussion_setup.set_topic(self.discussion, topic)
        self._notify()
        return result

    def set_participant_role(self, entity_id: int,
                             participant_role: str = "standard") -> dict:
        """Set or change a participant's role (e.g. devils_advocate)."""
        result = app_discussion_roles.set_participant_role(
            self.discussion, self.db, entity_id, participant_role,
        )
        self._notify()
        return result

    def set_discussion_method(self, method_name: str) -> dict:
        """Set the discussion method (e.g. 'ach', 'belief_diffusion').

        Must be called before starting the discussion.
        """
        try:
            result = app_discussion_setup.set_discussion_method(
                self.discussion, method_name,
            )
        except ValueError as e:
            # Usually an unknown name, but a method class's own constructor
            # can raise ValueError too, so leave a trace either way.
            logger.warning("Could not set discussion method %r: %s", method_name, e)
            return {"error": str(e)}
        self._notify()
        return result

    def list_discussion_methods(self) -> list[dict]:
        """Return metadata for all available discussion methods."""
        from ..methods import list_methods
        return list_methods()

    async def recommend_method(self, topic: str, answer_type: str) -> dict:
        """Get LLM-based method recommendations for a topic."""
        mod = self.discussion.moderator
        if not mod or not mod.ai_config:
            return {"error": "No AI moderator configured for recommendations"}
        api_key = self._resolve_key_for_moderator(
            mod.ai_config.provider_id, "",  # env var looked up by resolver
        )
        from ..ai_client import AIClient
        ai_client = AIClient(
            base_url=mod.ai_config.base_url, api_key=api_key,
        )
        provider = {"model": mod.ai_config.model}
        # Panel models already added to the roster (see the New Discussion
        # tab layout: participants are picked before "Suggest Method" is
        # clicked) let the recommender down-rank structured-output methods
        # when a participant's model is known to lack tool support (#23).
        panel_models = [
            (e.ai_config.model, e.ai_config.base_url)
            for e in self.discussion.entities
            if e.entity_type == EntityType.AI and e.ai_config
        ]
        try:
            recs = await app_discussion_setup.recommend_method(
                topic, answer_type, ai_client, provider,
                db=self.db, panel_models=panel_models,
            )
            return {"recommendations": recs}
        except Exception as e:
            logger.exception("Method recommendation failed")
            return {"error": str(e)}
        finally:
            await ai_client.close()

    # ------------------------------------------------------------------
    # Discussion lifecycle
    # ------------------------------------------------------------------

    async def start_discussion(self, moderator_participates: bool = False,
                               max_rounds: int = 0,
                               cost_limit: float = 0.0) -> dict:
        """Start a new discussion with the configured entities and topic.

        Async only to let the tool-capability gate wait for an in-flight
        model-catalogue download first (#118).
        """
        await self.db.pricing.wait_for_refresh()
        result = app_discussion_setup.start_discussion(
            self.discussion, self.db, self.moderator,
            moderator_participates, max_rounds, cost_limit,
        )
        self._notify()
        if "error" in result:
            return result
        return self.get_state()

    def set_cost_limit(self, cost_limit: float) -> dict:
        """Update the cost limit for the current discussion."""
        self.discussion.cost_limit = cost_limit
        if self.discussion.id:
            self.db.update_discussion(self.discussion.id, cost_limit=cost_limit)
        self._notify()
        return self.get_state()

    def set_default_context_strategy(self, strategy: str,
                                     window_size: int = 20) -> dict:
        """Set the discussion-level default context strategy."""
        from ..context_strategies import ContextStrategy
        try:
            ContextStrategy(strategy)
        except ValueError:
            return {"error": f"Unknown context strategy: {strategy!r}"}
        self.discussion.default_context_strategy = strategy
        self.discussion.default_context_window_size = int(window_size)
        if self.discussion.id:
            self.db.update_discussion(
                self.discussion.id,
                default_context_strategy=strategy,
                default_context_window_size=int(window_size),
            )
        self._notify()
        return self.get_state()

    def set_member_context_strategy(self, entity_id: int, strategy: str,
                                    window_size: int = 20) -> dict:
        """Set a per-entity context strategy override."""
        from ..context_strategies import ContextStrategy
        try:
            ContextStrategy(strategy)
        except ValueError:
            return {"error": f"Unknown context strategy: {strategy!r}"}
        entity = self.discussion.get_entity(entity_id)
        if not entity:
            return {"error": "Entity not in discussion"}
        # Always store in memory (for setup phase before discussion.id exists)
        self.discussion.member_context_configs[entity_id] = {
            "strategy": strategy, "window_size": int(window_size),
        }
        if self.discussion.id:
            self.db.update_member_context_strategy(
                self.discussion.id, entity_id, strategy, int(window_size),
            )
        self._notify()
        return self.get_state()

    def submit_human_message(self, entity_id: int, content: str) -> dict:
        """Submit a message from a human participant."""
        result = app_discussion_flow.submit_human_message(
            self.discussion, self.db, entity_id, content,
        )
        self._notify()
        return result

    def submit_human_structured_message(
            self, entity_id: int, payload: dict) -> dict:
        """Submit a validated structured payload from a human participant."""
        result = app_discussion_flow.submit_human_structured_message(
            self.discussion, self.db, entity_id, payload,
        )
        self._notify()
        return result

    def submit_moderator_message(self, content: str) -> dict:
        """Submit a message from the human moderator."""
        result = app_discussion_flow.submit_moderator_message(
            self.discussion, self.db, content,
        )
        self._notify()
        return result

    async def generate_ai_turn(self) -> dict:
        """Generate an AI participant's contribution for the current turn."""
        result = await app_discussion_flow.generate_ai_turn(
            self.discussion, self.moderator, self.db, self.db.pricing,
            key_resolver=self._resolve_key_for_moderator,
        )
        self._notify()
        return result

    async def complete_turn(self, moderator_summary: str = "") -> dict:
        """Complete the current turn: generate or accept summary, advance turn order."""
        result = await app_discussion_flow.complete_turn(
            self.discussion, self.moderator, self.db, self.db.pricing,
            self.get_state, moderator_summary,
        )
        self._notify()
        return result

    def reassign_turn(self, entity_id: int) -> dict:
        """Reassign the current turn to a different participant."""
        result = app_discussion_flow.reassign_turn(self.moderator, entity_id)
        if "error" not in result:
            self._notify()
            result["state"] = self.get_state()
        return result

    async def mediate(self, context: str = "") -> dict:
        """Have the moderator intervene to mediate a conflict."""
        result = await app_discussion_flow.mediate(
            self.discussion, self.moderator, self.db, self.db.pricing, context,
        )
        self._notify()
        return result

    async def conclude_discussion(self) -> dict:
        """End the discussion, generating a final synthesis if the moderator is AI.

        Returns the full state dict, carrying ``conclusion_error`` when the
        synthesis could not be generated or could not be recorded. The
        frontend feeds the return value straight to ``onStateUpdate``, so
        the key has to ride along on the state rather than be returned
        beside it (golden rule 6, issue #71).
        """
        self._cancel_pending_user_inputs()
        result = await app_discussion_flow.conclude_discussion(
            self.discussion, self.moderator, self.db, self.db.pricing,
        )
        self._notify()
        state = self.get_state()
        # The frontend feeds this straight to onStateUpdate, so a failed
        # synthesis has to ride along on the state dict or it is lost
        # before it ever reaches the UI (golden rule 6, issue #71).
        if result.get("conclusion_error"):
            state["conclusion_error"] = result["conclusion_error"]
        return state
