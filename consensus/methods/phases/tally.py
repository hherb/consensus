"""Tally phase handler for Voting Method.

The moderator announces vote results and synthesises the outcome.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class TallyHandler(PhaseHandler):
    """Phase 3: Results announcement and synthesis."""

    phase = Phase(
        name="tally",
        display_name="Results & Synthesis",
        description=(
            "The moderator announces vote results and synthesises "
            "the group's decision."
        ),
        rounds=1,
    )

    # Moderator-only phase — prompts are empty because the moderator
    # tallies and announces results; participants do not speak.

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All votes are in.  The moderator will now tally the "
            "results and present the outcome."
        )
