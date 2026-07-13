"""Nominal Group Technique — structured brainstorming (issue #24).

The catalog's first *generative* method: instead of critiquing an
existing position, the group creates and prioritises options.

Phases:
  1. GENERATE  — Silent, anonymised, independent idea generation
  2. CLUSTER   — Moderator merges duplicates into a candidate list
  3. CLARIFY   — One round of questions/refinement (no advocacy)
  4. ALLOCATE  — Each participant distributes a fixed point pool
  5. RANK      — Moderator presents the ranked shortlist
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases._ngt_helpers import (
    POINTS_PER_VOTER,
    entities_with_allocations,
    format_ranked_candidates,
)
from .phases.allocate_points import AllocatePointsHandler
from .phases.clarify_ideas import ClarifyIdeasHandler
from .phases.cluster_ideas import ClusterIdeasHandler
from .phases.generate_ideas import GenerateIdeasHandler
from .phases.rank_ideas import RankIdeasHandler

if TYPE_CHECKING:
    from ..models import Discussion


class NominalGroupTechnique(DiscussionMethod):
    """Nominal Group Technique — generate, consolidate, prioritise."""

    name = "nominal_group"
    display_name = "Nominal Group Technique"
    description = (
        "Structured brainstorming for generating and prioritising "
        "options.  Participants silently and independently propose "
        "ideas (anonymised), the moderator merges duplicates into a "
        "candidate list, one clarification round ensures shared "
        "understanding, then each participant distributes a fixed pool "
        "of points across candidates.  Produces a ranked shortlist.  "
        "Best for open problem-solving where the group must create "
        "options, not just evaluate a position."
    )
    phase_handlers = (
        GenerateIdeasHandler(),
        ClusterIdeasHandler(),
        ClarifyIdeasHandler(),
        AllocatePointsHandler(),
        RankIdeasHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        ranked = format_ranked_candidates(state)
        n_voters = len(entities_with_allocations(state))
        return (
            "The Nominal Group Technique process is complete.\n\n"
            f"Final ranking ({n_voters} participant(s) allocated "
            f"{pool} points each):\n{ranked}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Ranked shortlist** — Present the top candidates in "
            "order with their point totals\n"
            "2. **Rationale** — Summarise why the leading candidates "
            "earned support, citing participants' stated reasons\n"
            "3. **Vote pattern** — Was support concentrated or split?  "
            "Note near-ties and polarised allocations\n"
            "4. **Preserved dissent** — Flag lower-ranked ideas with "
            "strongly argued support worth revisiting\n"
            "5. **Next steps** — Recommend how to take the top "
            "candidates forward.\n\n"
            "Present actual point totals and cite specific rationales."
        )
