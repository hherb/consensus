"""Analysis of Competing Hypotheses (ACH) — structured analytic method.

Developed by Richards Heuer for the CIA, ACH is designed to overcome
cognitive biases in analytical judgement.  Rather than seeking evidence
to confirm a favoured hypothesis, ACH evaluates ALL hypotheses against
ALL evidence simultaneously, focusing on *disconfirming* evidence.

Phases:
  1. HYPOTHESIZE — All participants propose competing hypotheses
  2. EVIDENCE    — Participants gather evidence (using tools)
  3. EVALUATE    — Each participant rates each hypothesis against each
                   piece of evidence: consistent (+), inconsistent (-),
                   or neutral (0)
  4. ANALYSE     — Moderator ranks hypotheses by inconsistency count,
                   identifies diagnostic evidence, checks for sensitivity
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases import (
    AnalyseACHHandler,
    EvaluateMatrixHandler,
    GatherEvidenceHandler,
    HypothesizeHandler,
)

if TYPE_CHECKING:
    from ..models import Discussion


class ACH(DiscussionMethod):
    """Analysis of Competing Hypotheses."""

    name = "ach"
    display_name = "Analysis of Competing Hypotheses"
    description = (
        "A structured analytic method from intelligence analysis.  "
        "Participants generate hypotheses, gather evidence, then "
        "systematically rate each hypothesis against each piece of "
        "evidence.  Hypotheses are ranked by inconsistency count — "
        "the one with the fewest inconsistencies is most likely."
    )
    phase_handlers = (
        HypothesizeHandler(),
        GatherEvidenceHandler(),
        EvaluateMatrixHandler(),
        AnalyseACHHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion (cross-phase)
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the ACH analysis prompt."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        ev_list = "\n".join(
            f"  E{e['id']}: {e['text']}"
            for e in evidence
        )

        # Aggregate matrix
        agg = self._aggregate_matrix(discussion)

        return (
            "The ACH evaluation is complete.  Provide a comprehensive "
            "analysis.\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Evidence:\n{ev_list}\n\n"
            f"Aggregated ratings (majority vote):\n{agg}\n\n"
            "Your analysis MUST include:\n"
            "1. **Hypothesis ranking** — Rank by inconsistency count "
            "(FEWER inconsistencies = more likely).  This is the core "
            "ACH insight: we reject hypotheses that are inconsistent "
            "with evidence, not confirm ones that are consistent.\n"
            "2. **Diagnostic evidence** — Which evidence items are most "
            "diagnostic (differentiate between hypotheses)?  Which are "
            "non-diagnostic (consistent with everything)?\n"
            "3. **Sensitivity analysis** — Would the ranking change if "
            "any single piece of evidence were removed?  If so, that "
            "evidence is a critical dependency.\n"
            "4. **Evaluator disagreements** — Where did participants "
            "disagree on ratings?  What does that tell us?\n"
            "5. **Conclusion** — Which hypothesis best survives scrutiny "
            "and why?  What would we need to investigate further?\n\n"
            "Ground your analysis in the data.  Do not speculate beyond "
            "what the evidence matrix supports."
        )

    # ------------------------------------------------------------------
    # Aggregation (cross-phase helper)
    # ------------------------------------------------------------------

    def _aggregate_matrix(self, discussion: Discussion) -> str:
        """Aggregate all evaluators' matrices by majority vote."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])
        all_matrices = state.get("matrix", {})

        if not all_matrices or not evidence:
            return "(No evaluation data)"

        lines = []
        inconsistency_counts: dict[str, int] = {}

        for hi, h in enumerate(hypotheses):
            hkey = f"H{hi+1}"
            inc_count = 0
            row_parts = []
            for e in evidence:
                ekey = f"E{e['id']}"
                votes = {"+" : 0, "-": 0, "0": 0}
                for _evaluator_id, matrix in all_matrices.items():
                    r = matrix.get(hkey, {}).get(ekey, "0")
                    if r in votes:
                        votes[r] += 1
                # Majority vote across evaluators; ties default to
                # neutral ("0") since ACH focuses on disconfirmation
                max_count = max(votes.values())
                tied = [k for k, v in votes.items() if v == max_count]
                majority = tied[0] if len(tied) == 1 else "0"
                row_parts.append(majority)
                if majority == "-":
                    inc_count += 1

            inconsistency_counts[hkey] = inc_count
            row_str = ", ".join(
                f"E{e['id']}:{r}" for e, r in zip(evidence, row_parts)
            )
            lines.append(f"  {hkey}: [{row_str}] — {inc_count} inconsistencies")

        # Rank
        lines.append("")
        lines.append("  Ranking (fewer inconsistencies = more likely):")
        for hkey, count in sorted(inconsistency_counts.items(),
                                  key=lambda x: x[1]):
            try:
                idx = int(hkey.lstrip("H")) - 1
            except ValueError:
                idx = -1
            label = hypotheses[idx] if 0 <= idx < len(hypotheses) else hkey
            lines.append(f"    {hkey} ({count} inc.): {label}")

        return "\n".join(lines)
