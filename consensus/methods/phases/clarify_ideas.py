"""Clarification phase handler for Nominal Group Technique.

One free-text round: participants make sure every candidate idea is
understood before voting — questions, ambiguities, overlaps, sharper
wording.  No advocacy or ranking yet, and no structured output tool
(this phase produces discussion, not data).  Context stays anonymised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import format_candidates

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ClarifyIdeasHandler(PhaseHandler):
    """Phase 3: One round of clarification on the candidate list."""

    phase = Phase(
        name="clarify",
        display_name="Clarification",
        description=(
            "One round of questions and refinement: participants make "
            "sure every candidate idea is understood before voting.  "
            "No advocacy or ranking yet."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        candidates_text = format_candidates(discussion.method_state)
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) session.\n"
            f"Topic: {discussion.topic}\n\n"
            "CLARIFICATION PHASE\n\n"
            "Review the candidate ideas below.  Ask clarifying "
            "questions, point out ambiguities or overlaps, and suggest "
            "sharper wording where a candidate is unclear.  Do NOT "
            "advocate for or rank candidates yet — voting comes next.\n\n"
            f"Candidate ideas:\n{candidates_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Raise anything you need "
            "to clarify about the candidate ideas — or state that the "
            "list is clear to you.  Refer to candidates by number.  Do "
            "not rank or advocate yet."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has raised their clarification points.  "
            "Briefly answer factual questions about what a candidate "
            f"means, then invite {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        n = len(state.get("candidates", []))
        candidates_text = format_candidates(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"The consolidated list has {n} candidate idea(s):\n"
            f"{candidates_text}\n\n"
            "One round of clarification follows: make sure every "
            "candidate is understood.  No advocacy or ranking yet."
        )
