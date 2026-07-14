"""Sensitivity-analysis phase handler for the Weighted Decision Matrix.

A moderator-only presentational phase (the ``rank_ideas.py`` pattern —
the moderator takes a real turn so the analysis lands in the
transcript): the weighted ranking, per-participant divergence, and
one-at-a-time sensitivity findings are all computed deterministically
by ``_mcda_analysis`` and embedded in the prompt.  The moderator only
*interprets* the numbers — how robust is the winner, which criteria
are pivotal, where do participants diverge — and never produces them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._mcda_analysis import (
    format_divergence,
    format_mean_score_matrix,
    format_sensitivity,
    format_weighted_ranking,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SensitivityHandler(PhaseHandler):
    """Phase 4: Moderator presents the computed sensitivity analysis."""

    phase = Phase(
        name="sensitivity",
        display_name="Sensitivity Analysis",
        description=(
            "The moderator reports how the ranking changes as weights "
            "vary, and flags close calls and divergence between "
            "participants."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks when presenting the analysis."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            "You are the moderator of a Weighted Decision Matrix "
            "(multi-criteria decision analysis) session, presenting "
            "the computed results.\n"
            f"Topic: {discussion.topic}\n\n"
            "SENSITIVITY ANALYSIS PHASE\n\n"
            "All numbers below are computed from the recorded weights "
            "and scores — present and interpret them; do not recompute "
            "or invent figures.\n\n"
            f"Weighted ranking:\n{format_weighted_ranking(state)}\n\n"
            f"Mean score matrix:\n{format_mean_score_matrix(state)}\n\n"
            "Participant divergence (spread of per-participant "
            f"weighted totals):\n{format_divergence(state)}\n\n"
            f"Sensitivity findings:\n{format_sensitivity(state)}\n\n"
            "Present: (1) the ranking and how decisive the margin is, "
            "(2) which criteria are pivotal — would the winner change "
            "if a weight shifted?  (3) where participants diverge most "
            "and what that disagreement is about, (4) whether the "
            "result is robust enough to decide on, or fragile enough "
            "that the group should revisit weights."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Present the sensitivity analysis now: the ranking, the "
            "margin, pivotal criteria, divergence, and your robustness "
            "assessment."
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > self.phase.rounds

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All scores are in.  Weighted ranking:\n"
            f"{format_weighted_ranking(state)}\n\n"
            "The moderator will now analyse how robust this ranking "
            "is to weight changes."
        )
