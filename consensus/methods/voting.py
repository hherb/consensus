"""Participant Voting — structured deliberation with formal motions and votes.

Participants deliberate, propose motions, vote, and see results tallied.
Supports multiple motion types and configurable vote thresholds.

Phases:
  1. DELIBERATE  — Open discussion; participants may propose motions
                   via JSON blocks in their responses
  2. VOTE        — Each participant votes on all pending motions
                   (for / against / abstain)
  3. TALLY       — Moderator announces results and synthesises
                   the outcome
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases._voting_helpers import format_tally, tally_votes
from .phases.deliberate import DeliberateHandler
from .phases.tally import TallyHandler
from .phases.vote import VoteHandler

if TYPE_CHECKING:
    from ..models import Discussion


class VotingMethod(DiscussionMethod):
    """Participant voting with formal motions and tallied results."""

    name = "voting"
    display_name = "Participant Voting"
    description = (
        "Structured deliberation followed by formal voting.  Participants "
        "discuss the topic, propose motions during deliberation, then vote "
        "on each motion (for / against / abstain).  Results are tallied "
        "with configurable thresholds (simple majority by default).  "
        "Best for decisions requiring clear group consensus."
    )
    phase_handlers = (
        DeliberateHandler(),
        VoteHandler(),
        TallyHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return conclusion prompt with vote tallies."""
        state = discussion.method_state
        tally = tally_votes(discussion)
        motions = state.get("motions", [])
        threshold = state.get("threshold", "simple_majority")

        tally_text = format_tally(tally, motions, discussion)

        return (
            "The voting process is complete.\n\n"
            f"Vote results:\n{tally_text}\n\n"
            f"Threshold: {threshold.replace('_', ' ')}\n\n"
            "Provide a synthesis:\n"
            "1. **Results** — State which motions passed and which failed\n"
            "2. **Analysis** — Analyse the voting patterns and rationale\n"
            "3. **Consensus assessment** — How strong is the group's "
            "agreement?  Were there significant dissents?\n"
            "4. **Recommendations** — Based on the vote outcomes, what "
            "are the next steps?\n\n"
            "Present the results clearly and cite specific reasoning "
            "from participants."
        )
