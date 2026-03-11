"""Diagnose phase handler for Belief Diffusion.

The moderator analyses belief trajectories, convergence patterns,
and consistency between stated reasoning and actual belief shifts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._belief_helpers import check_convergence

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class DiagnoseHandler(PhaseHandler):
    """Phase 4: Moderator analyses belief trajectories."""

    phase = Phase(
        name="diagnose",
        display_name="Diagnosis",
        description=(
            "The moderator analyses the full belief trajectory: which arguments "
            "caused the largest shifts, where disagreement persists, and whether "
            "stated reasoning is consistent with actual belief changes."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    def get_transition_message(self, discussion: Discussion) -> str:
        converged = check_convergence(discussion)
        reason = "converged" if converged else "reached the round limit"
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Belief diffusion has {reason}.  "
            "The moderator will now analyse the full belief trajectory."
        )
