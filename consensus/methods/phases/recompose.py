"""Recompose phase handler for Recursive Decomposition.

Participants synthesize all sub-question analyses and integration
insights into a coherent, unified answer to the original question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class RecomposeHandler(PhaseHandler):
    """Phase 4: Synthesis into a unified answer."""

    phase = Phase(
        name="recompose",
        display_name="Recomposition",
        description=(
            "Synthesize all sub-question analyses and integration "
            "insights into a coherent, unified answer to the original "
            "question."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "RECOMPOSITION PHASE\n\n"
            "All sub-questions have been analyzed and cross-cutting "
            "patterns identified. Now synthesize everything into a "
            "coherent, unified answer to the original question.\n\n"
            "Your synthesis should:\n"
            "- Draw on the sub-question analyses and integration "
            "insights\n"
            "- Account for conflicts and uncertainties identified\n"
            f"- Present a clear, well-structured answer to: "
            f"\"{discussion.topic}\"\n"
            "- Note any aspects that remain unresolved or would benefit "
            "from deeper decomposition"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Synthesize everything "
            "into a coherent answer to the original question."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their synthesis. Briefly note "
            "how it compares to prior syntheses and what new "
            f"perspectives it brings. Next: {next_speaker_name}."
        )
