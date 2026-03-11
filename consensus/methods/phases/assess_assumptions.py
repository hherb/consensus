"""Assess Assumptions phase handler for Key Assumptions Check.

The moderator synthesises which assumptions hold, which are
vulnerable, and how this affects the analysis.  The system prompt
is empty because the moderator handles assessment directly via
the conclusion prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AssessAssumptionsHandler(PhaseHandler):
    """Phase 3: Moderator assessment of assumptions."""

    phase = Phase(
        name="assess",
        display_name="Assessment",
        description=(
            "The moderator assesses each assumption's status: "
            "confirmed, unsupported, or contested.  Identifies which "
            "vulnerable assumptions most affect the overall analysis."
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
            "All challenges are in.  The moderator will now assess "
            "each assumption's status and identify which vulnerable "
            "assumptions most affect the analysis."
        )
