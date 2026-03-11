"""Synthesise phase handler for Delphi Method.

The moderator presents the final distribution and analyses the
convergence pattern.  Identities are revealed (no anonymisation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import check_convergence

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SynthesiseDelphiHandler(PhaseHandler):
    """Phase 3: Synthesis — moderator summarises, identities revealed."""

    phase = Phase(
        name="synthesise",
        display_name="Synthesis",
        description=(
            "The moderator presents the final distribution and "
            "analyses the convergence pattern."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles synthesis

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    # ------------------------------------------------------------------
    # Context filtering — NO anonymisation (reveals identities)
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion) -> str:
        return content

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO synthesise)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        converged = check_convergence(discussion)
        reason = "converged" if converged else "reached the round limit"
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The Delphi process has {reason}.  "
            "The moderator will now synthesise the final distribution."
        )
