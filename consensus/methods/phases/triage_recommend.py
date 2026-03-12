"""Recommend phase handler for Guided Triage.

Moderator-only phase: synthesizes intake responses, calls
MethodRecommender programmatically, and presents recommendations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class TriageRecommendHandler(PhaseHandler):
    """Phase 2: Moderator synthesizes and recommends methods."""

    phase = Phase(
        name="recommend",
        display_name="Method Recommendation",
        description=(
            "The moderator synthesizes the intake responses and "
            "recommends discussion methods for the group to consider."
        ),
        rounds=1,
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "recommendations": [],
            "recommended_method": None,
            "chosen_method": None,
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Moderator only."""
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator conducting a methodology selection "
            "process. Based on the participants' answers about the "
            "problem type, decision context, and uncertainty structure, "
            "synthesize their input into a clear problem characterization.\n\n"
            "Focus on: what kind of problem this is, what the key "
            "uncertainties are, and what kind of analytical approach "
            "would be most productive."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Review the intake responses from participants above.\n\n"
            "Synthesize their answers into a clear characterization of:\n"
            "1. The type of problem or question\n"
            "2. The decision context and stakes\n"
            "3. The structure of uncertainty\n"
            "4. Whether there is a preliminary conclusion to test\n\n"
            "Based on this characterization, explain what kind of "
            "analytical method would be most productive and why."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Store the moderator's synthesis for the recommender call."""
        state = discussion.method_state
        state["moderator_characterization"] = content
        return ProcessedResponse(display_content=content)
