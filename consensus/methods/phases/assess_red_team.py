"""Assess phase handler for Red Team / Blue Team.

The moderator evaluates which attacks succeeded, what was revised,
and how robust the final position is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AssessRedTeamHandler(PhaseHandler):
    """Phase 4: Moderator assessment."""

    phase = Phase(
        name="assess",
        display_name="Assessment",
        description=(
            "The moderator evaluates which attacks succeeded, what "
            "was revised, and how robust the final position is."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles assessment

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Revision is complete.  The moderator will now assess "
            "the robustness of the final position."
        )
