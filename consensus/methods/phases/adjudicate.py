"""Adjudicate phase handler for Adversarial Collaboration.

The moderator evaluates the evidence against the pre-agreed criteria
and declares a verdict.  System/turn prompts are empty since the
moderator handles adjudication via get_conclusion_prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AdjudicateHandler(PhaseHandler):
    """Phase 4: Moderator adjudication."""

    phase = Phase(
        name="adjudicate",
        display_name="Adjudication",
        description=(
            "The moderator evaluates the evidence against the "
            "pre-agreed criteria and declares a verdict."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles adjudication

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All evidence has been presented.  The moderator will now "
            "evaluate the evidence against the pre-agreed criteria "
            "and render a verdict."
        )
