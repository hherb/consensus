"""Tree of Thoughts — iterative parallel exploration (issue #26).

An LLM-native problem-solving method exploiting parallel candidate
exploration: participants independently propose solution approaches
(anonymised to avoid anchoring), everyone scores them on feasibility/
impact/risk, a deterministic beam prune keeps the strongest few, the
survivors get a deep-dive round (refine + obstacles), and the
score→prune→expand loop repeats until the ordered beam stabilises or
the depth budget is spent — then the moderator synthesises.

Phases:
  1. PROPOSE    — Anonymised independent approach generation
  2. SCORE      — Everyone scores eligible approaches (3 fixed dimensions)
  3. PRUNE      — Deterministic beam cut; routes the loop (issue #22)
  4. EXPAND     — Deep-dive of survivors; loops back to SCORE
  5. SYNTHESISE — Moderator presents the outcome
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases._tot_analysis import format_artifact_digest
from .phases._tot_helpers import (
    MAX_PROPOSE_ROUNDS,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    thought_label,
)
from .phases.expand_thoughts import ExpandThoughtsHandler
from .phases.propose_thoughts import ProposeThoughtsHandler
from .phases.prune_thoughts import PruneThoughtsHandler
from .phases.score_thoughts import ScoreThoughtsHandler
from .phases.synthesise_thoughts import SynthesiseThoughtsHandler

if TYPE_CHECKING:
    from ..models import Discussion


class TreeOfThoughts(DiscussionMethod):
    """Tree of Thoughts — generate, score, prune, expand, synthesise."""

    name = "tree_of_thoughts"
    display_name = "Tree of Thoughts"
    description = (
        "Iterative parallel exploration for open problem-solving.  "
        "Participants independently propose distinct solution "
        "approaches (anonymised), everyone scores them on feasibility, "
        "impact, and risk, and a deterministic beam prune keeps the "
        "strongest few.  Survivors get a deep-dive round — refinements "
        "and obstacles — and are re-scored; the loop repeats until the "
        "ranking stabilises or a depth budget is spent.  Produces a "
        "recommended approach with its score trajectory and known "
        "obstacles.  Best for open questions where the solution space "
        "should be explored broadly before committing."
    )
    phase_handlers = (
        ProposeThoughtsHandler(),
        ScoreThoughtsHandler(),
        PruneThoughtsHandler(),
        ExpandThoughtsHandler(),
        SynthesiseThoughtsHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        artifact = state.get("tot_artifact", {})
        recommendation = artifact.get("recommendation") or {}
        stop_reason = artifact.get("stop_reason", "")
        if not stop_reason:
            # The exploration never finished: the propose phase aborted
            # with zero thoughts, the user concluded mid-method, or the
            # loop guard tripped — there is no outcome to synthesise
            # and no narrative ("depth budget spent") may be invented.
            return self._incomplete_conclusion_prompt(state)
        header = (
            "The Tree of Thoughts exploration is complete.  All numbers "
            "below were computed deterministically from the "
            "participants' scores — do not alter them.\n\n"
            f"{format_artifact_digest(state)}\n\n"
        )
        if stop_reason == STOP_CONVERGED and recommendation:
            outcome = (
                "The beam stabilised — the group's evaluation settled.  "
                "Provide a comprehensive synthesis:\n"
                f"1. **Recommended approach** — Present "
                f"{thought_label(recommendation['id'])} "
                f"(composite {recommendation['composite']}): why it won "
                "on the scored dimensions\n"
                "2. **How it sharpened** — Trace how the deep-dives "
                "refined and concretised it across passes\n"
                "3. **Known obstacles** — The obstacles raised against "
                "it and how they might be mitigated\n"
                "4. **Runners-up** — What the other survivors still "
                "offer and when they would be the better choice\n"
                "5. **Next steps** — Concrete first actions for the "
                "recommended approach."
            )
        elif stop_reason == STOP_DEGENERATE:
            outcome = (
                "The tree was degenerate — too few approaches survived "
                "to explore in parallel.  Provide a synthesis:\n"
                "1. **The surviving approach** — Present it with its "
                "scores and the reasoning behind them\n"
                "2. **Depth of support** — How much scoring coverage "
                "the outcome actually has (mind the caveats)\n"
                "3. **What is missing** — What a broader candidate set "
                "might have surfaced\n"
                "4. **Next steps** — Whether to proceed with the "
                "survivor or re-run generation with a reframed topic."
            )
        else:
            outcome = (
                "The depth budget was spent before the ranking "
                "stabilised — the exploration ended while still moving.  "
                "Provide a synthesis:\n"
                "1. **State of the beam** — The surviving approaches "
                "and their latest composites\n"
                "2. **What kept moving** — Which rankings changed "
                "between passes and why (cite the deep-dives)\n"
                "3. **Provisional recommendation** — The top-ranked "
                "approach, clearly flagged as unsettled\n"
                "4. **Next steps** — What further evidence or "
                "iteration would settle the ranking."
            )
        return header + outcome

    @staticmethod
    def _incomplete_conclusion_prompt(state: dict) -> str:
        """Honest conclusion instructions when no outcome was recorded."""
        if not state.get("thoughts"):
            return (
                "The Tree of Thoughts session ended before any "
                "candidate approaches were collected (the generation "
                f"phase found nothing usable within "
                f"{MAX_PROPOSE_ROUNDS} rounds).\n\n"
                "Provide a brief, honest wrap-up:\n"
                "1. **What happened** — State plainly that no "
                "approaches were generated, so there is no ranking and "
                "no recommendation\n"
                "2. **Likely cause** — Whether the topic was too "
                "narrow, ambiguous, or not an open problem\n"
                "3. **Next steps** — How to rephrase the topic (e.g. "
                "as a 'How might we…' question) for a fresh session.\n\n"
                "Do NOT invent approaches, scores, or a recommendation."
            )
        return (
            "The Tree of Thoughts exploration ended before completing "
            "(no final prune outcome was recorded).\n\n"
            "Provide a brief, honest wrap-up of what exists so far:\n"
            "1. **Collected approaches** — Summarise the candidate "
            "approaches that were proposed\n"
            "2. **State of evaluation** — How far scoring/pruning got, "
            "citing only numbers that actually appear in the "
            "discussion\n"
            "3. **Next steps** — What remained to be done and whether "
            "re-running the method is worthwhile.\n\n"
            "Do NOT invent a ranking, a convergence claim, or a "
            "recommendation — the exploration did not finish."
        )
