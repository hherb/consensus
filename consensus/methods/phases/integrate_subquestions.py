"""Integrate phase handler for Recursive Decomposition.

Participants examine sub-question analyses as a whole, identifying
reinforcements, conflicts, gaps, and emergent insights.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class IntegrateSubquestionsHandler(PhaseHandler):
    """Phase 3: Cross-cutting integration of sub-question analyses."""

    phase = Phase(
        name="integrate",
        display_name="Integration",
        description=(
            "Examine all sub-question analyses as a whole to identify "
            "reinforcements, conflicts, gaps, and emergent insights."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "INTEGRATION PHASE\n\n"
            "The sub-questions and all participants' analyses are in "
            "the discussion history. Examine them as a whole and "
            "identify:\n\n"
            "1. **Reinforcements** — Where do different sub-question "
            "analyses support the same conclusion?\n"
            "2. **Conflicts** — Where do analyses of different "
            "sub-questions point in contradictory directions?\n"
            "3. **Gaps** — What important connections or dependencies "
            "between sub-questions were missed in the analysis phase?\n"
            "4. **Emergent insights** — What becomes visible only when "
            "looking across all sub-questions together?"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Examine all sub-question "
            "analyses as a whole. What patterns, contradictions, or "
            "gaps emerge?"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has identified cross-cutting patterns. "
            "Briefly note the key reinforcements, conflicts, and gaps "
            f"found. Next: {next_speaker_name}."
        )
