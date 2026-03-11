"""Counterfactual Stress Testing — systematically test claim importance.

For each key claim in a developing consensus, invert it and check
if the conclusion survives. Produces a ranked classification of
claims as load-bearing, supportive, or decorative.

Phases:
  1. CF_DELIBERATE — Open discussion to establish preliminary conclusion
                     (skipped if prior_conclusion is provided)
  2. EXTRACT       — Moderator extracts 3-7 key falsifiable claims
  3. STRESS_TEST   — Invert each claim; participants assess impact
  4. SYNTHESIZE    — Moderator classifies claims and assesses robustness
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.counterfactual_deliberate import CounterfactualDeliberateHandler
from .phases.counterfactual_extract import ExtractClaimsHandler
from .phases.counterfactual_stress import StressTestHandler
from .phases.counterfactual_synthesize import SynthesizeHandler
from .phases._counterfactual_helpers import classify_claim, format_results_table

if TYPE_CHECKING:
    from ..models import Discussion

logger = logging.getLogger(__name__)


class CounterfactualStressTest(DiscussionMethod):
    """Counterfactual Stress Testing — test which beliefs are load-bearing."""

    name = "counterfactual"
    display_name = "Counterfactual Stress Testing"
    description = (
        "Systematically tests which beliefs are load-bearing vs. decorative "
        "in a developing consensus. For each key claim, participants argue "
        "from the premise that it is false and score the impact. Produces "
        "a ranked classification of claims by structural importance."
    )
    phase_handlers = (
        CounterfactualDeliberateHandler(),
        ExtractClaimsHandler(),
        StressTestHandler(),
        SynthesizeHandler(),
    )

    # ------------------------------------------------------------------
    # State initialization
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Initialize state, skipping deliberation if prior_conclusion set."""
        # Read prior_conclusion from the discussion's existing method_state
        # BEFORE super().init_state() runs, because super() merges handler
        # init_state dicts which reset prior_conclusion to None.
        prior = (discussion.method_state or {}).get("prior_conclusion")

        state = super().init_state(discussion)

        if prior:
            state["current_phase"] = "extract"
            state["prior_conclusion"] = prior
            state["preliminary_conclusion"] = prior
            logger.info("Prior conclusion provided — skipping deliberation")

        return state

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        """Increment phase_round; finalize claim scores during stress_test."""
        super().on_round_complete(discussion)

        phase = self.current_phase(discussion)
        if phase and phase.name == "stress_test":
            state = discussion.method_state
            idx = state.get("current_claim_index", 0)
            claim_results = state.get("claim_results", [])

            if idx < len(claim_results):
                scores = claim_results[idx].get("scores", {})
                if scores:
                    avg = sum(scores.values()) / len(scores)
                    claim_results[idx]["avg_score"] = avg
                    claim_results[idx]["classification"] = classify_claim(avg)

            state["current_claim_index"] = idx + 1

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Build the final synthesis prompt with results table."""
        state = discussion.method_state
        claims = state.get("claims", [])
        claim_results = state.get("claim_results", [])
        conclusion = (state.get("preliminary_conclusion")
                      or state.get("prior_conclusion")
                      or "(no conclusion)")

        if not claims:
            return (
                "The counterfactual stress test could not extract any "
                "claims from the discussion. Please provide a qualitative "
                "summary of the discussion and note that claim extraction "
                "was unsuccessful."
            )

        table = format_results_table(claim_results)

        return (
            "The counterfactual stress test is complete.\n\n"
            f"**Preliminary conclusion:** {conclusion}\n\n"
            f"**Stress test results:**\n{table}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Claim ranking** — Rank claims from most to least "
            "structurally important based on their impact scores.\n"
            "2. **Load-bearing analysis** — For each LOAD-BEARING claim "
            "(avg >= 4.0), explain why the conclusion depends on it.\n"
            "3. **Decorative analysis** — For each DECORATIVE claim "
            "(avg < 2.0), explain why it is not structurally important.\n"
            "4. **Robustness assessment** — Overall, how robust is the "
            "conclusion? How many of its supporting claims are truly "
            "load-bearing vs. decorative?\n"
            "5. **Revised conclusion** — Given what the stress test "
            "revealed, restate the conclusion with appropriate "
            "confidence and caveats.\n\n"
            "Be specific and cite the impact scores."
        )
