"""Weighted Decision Matrix — multi-criteria decision analysis (issue #25).

The catalog's structured decision method: instead of debating to a
prose synthesis, the group enumerates options, agrees weighted
criteria, scores every option against every criterion, reviews a
deterministic sensitivity analysis, and records a machine-readable
decision artifact (``method_state["decision_artifact"]``) that the
storyboard, MCP server, or a follow-up discussion can consume.

Phases:
  1. OPTIONS      — Enumerate the decision alternatives
  2. CRITERIA     — Jointly define criteria and agree weights
  3. SCORE        — Every participant scores option × criterion
  4. SENSITIVITY  — Moderator presents computed robustness analysis
  5. DECIDE       — Moderator records the structured decision
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases._mcda_analysis import (
    format_decision_artifact,
    format_divergence,
    format_sensitivity,
    format_weighted_ranking,
)
from .phases.analyse_sensitivity import SensitivityHandler
from .phases.decide import DecideHandler
from .phases.enumerate_options import EnumerateOptionsHandler
from .phases.score_options import ScoreOptionsHandler
from .phases.weight_criteria import WeightCriteriaHandler

if TYPE_CHECKING:
    from ..models import Discussion


class WeightedDecisionMatrix(DiscussionMethod):
    """Weighted Decision Matrix — enumerate, weigh, score, decide."""

    name = "decision_matrix"
    display_name = "Weighted Decision Matrix (MCDA)"
    description = (
        "Multi-criteria decision analysis for choosing between "
        "options.  Participants enumerate the alternatives, jointly "
        "define weighted decision criteria, and score every option "
        "against every criterion.  Weighted totals produce a ranked "
        "result with a sensitivity analysis (does the winner survive "
        "weight changes?) and a structured, machine-readable decision "
        "artifact.  Best for making a concrete decision between "
        "identifiable options."
    )
    phase_handlers = (
        EnumerateOptionsHandler(),
        WeightCriteriaHandler(),
        ScoreOptionsHandler(),
        SensitivityHandler(),
        DecideHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        artifact = state.get("decision_artifact")
        results = (format_decision_artifact(artifact) if artifact
                   else f"Weighted ranking:\n"
                        f"{format_weighted_ranking(state)}")
        return (
            "The Weighted Decision Matrix process is complete.\n\n"
            f"{results}\n\n"
            f"Sensitivity findings:\n{format_sensitivity(state)}\n\n"
            f"Participant divergence:\n{format_divergence(state)}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Decision** — the recommended option and its weighted "
            "score\n"
            "2. **Rationale** — why it won: which weighted criteria "
            "drove the result\n"
            "3. **Runner-up analysis** — how close the alternatives "
            "were and what would have made them win\n"
            "4. **Divergence** — where participants disagreed most and "
            "what the disagreement is about\n"
            "5. **Robustness** — the sensitivity findings: would the "
            "decision change if weights shifted?\n"
            "6. **Caveats & next steps** — conditions under which the "
            "decision should be revisited.\n\n"
            "Present the actual weighted totals; cite participants' "
            "stated rationales."
        )
