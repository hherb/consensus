"""Analysis phase handler for Analysis of Competing Hypotheses.

The moderator analyses the evidence matrix, ranks hypotheses, and
identifies diagnostic evidence.  Prompts are empty — the moderator
handles analysis via the conclusion prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AnalyseACHHandler(PhaseHandler):
    """Phase 4: Moderator analysis of the evidence matrix."""

    phase = Phase(
        name="analyse",
        display_name="Analysis",
        description=(
            "The moderator analyses the evidence matrix, ranks "
            "hypotheses, identifies the most diagnostic evidence, "
            "and checks for sensitivity to key assumptions."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles analysis

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All evaluations are in.  The moderator will now analyse "
            "the evidence matrix and rank the hypotheses."
        )
