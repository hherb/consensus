"""Counterfactual Deliberate phase handler.

Open discussion to establish a preliminary conclusion before
stress testing. Skipped if a prior_conclusion is provided
(handled in CounterfactualStressTest.init_state).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class CounterfactualDeliberateHandler(PhaseHandler):
    """Phase 1: Open deliberation to establish a preliminary conclusion."""

    phase = Phase(
        name="cf_deliberate",
        display_name="Deliberation",
        description=(
            "Open discussion to establish a preliminary position on the "
            "topic before stress testing begins."
        ),
        rounds=2,
        allow_tools=True,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "preliminary_conclusion": None,
            "prior_conclusion": None,
        }

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a preliminary "
            f"discussion to establish a position.\n"
            f"Topic: {discussion.topic}\n\n"
            "Discuss openly and work toward a preliminary conclusion. "
            "Share your perspective, engage with others' arguments, and "
            "try to identify the strongest position supported by the "
            "available reasoning and evidence."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Share your perspective "
            "on this topic. Build on others' contributions where possible."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has shared their perspective. "
            "Briefly summarize their key points and note areas of "
            f"agreement or disagreement. Next: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Capture moderator's final-round summary as preliminary_conclusion."""
        state = discussion.method_state
        if (entity.id == discussion.moderator_id
                and state.get("phase_round", 1) >= self.phase.rounds):
            state["preliminary_conclusion"] = content
        return ProcessedResponse(display_content=content)
