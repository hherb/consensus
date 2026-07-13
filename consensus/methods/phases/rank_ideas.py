"""Ranked-results phase handler for Nominal Group Technique.

A moderator-only phase (the moderator takes a real turn so the ranked
synthesis lands in the transcript — see frame_hypotheses.py for the
pattern): the point totals are tallied and the moderator presents the
ranked shortlist.  Identities are revealed from here on (no
anonymisation), mirroring Delphi's synthesise phase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._ngt_helpers import format_ranked_candidates

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class RankIdeasHandler(PhaseHandler):
    """Phase 5: Moderator presents the ranked shortlist."""

    phase = Phase(
        name="rank",
        display_name="Ranked Results",
        description=(
            "The moderator presents the ranked shortlist and "
            "synthesises the group's priorities."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks when presenting results."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        ranked = format_ranked_candidates(discussion.method_state)
        return (
            "You are the moderator of a Nominal Group Technique (NGT) "
            "session presenting the voting results.\n"
            f"Topic: {discussion.topic}\n\n"
            "RANKED RESULTS PHASE\n\n"
            f"Point totals:\n{ranked}\n\n"
            "Present the ranked shortlist with a short rationale for "
            "each top candidate, note how concentrated or split the "
            "vote was, and flag any low-scoring idea that received a "
            "strongly argued allocation."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Present the ranked results now: the shortlist in point "
            "order, the vote pattern, and notable rationales."
        )

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
            "All point allocations are in.  The moderator will now "
            "present the ranked shortlist:\n\n"
            + format_ranked_candidates(discussion.method_state)
        )
