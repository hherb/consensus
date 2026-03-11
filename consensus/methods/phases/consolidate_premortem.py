"""Consolidate Premortem phase handler for Premortem Analysis.

The moderator consolidates all failure narratives, identifies the most
plausible and most dangerous failure modes, and recommends mitigations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ConsolidatePremortemHandler(PhaseHandler):
    """Phase 3: Moderator consolidation of failure modes."""

    phase = Phase(
        name="consolidate",
        display_name="Consolidation",
        description=(
            "The moderator consolidates all failure narratives, "
            "identifies the most plausible and most dangerous failure "
            "modes, and recommends mitigations."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles consolidation

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All failure narratives are in.  The moderator will now "
            "consolidate failure modes, rank them by plausibility and "
            "severity, and recommend mitigations."
        )
