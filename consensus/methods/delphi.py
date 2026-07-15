"""Delphi Method — anonymous iterative expert forecasting.

Participants provide independent estimates across multiple rounds.
After each round, the moderator shares the statistical distribution
and anonymised reasoning.  Participants then revise their estimates.
Avoids anchoring, authority bias, and social pressure.

Phases:
  1. ESTIMATE    — Each participant provides an independent estimate
                   with reasoning (round 1)
  2. REVISE      — Moderator shares distribution + anonymised reasoning;
                   participants revise (rounds 2+, until convergence)
  3. SYNTHESISE  — Moderator presents the final distribution and
                   analyses the convergence pattern
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.estimate import EstimateHandler
from .phases.revise_delphi import ReviseDelphiHandler
from .phases.synthesise_delphi import SynthesiseDelphiHandler

if TYPE_CHECKING:
    from ..models import Discussion


class DelphiMethod(DiscussionMethod):
    """Delphi Method — anonymous iterative convergence."""

    name = "delphi"
    display_name = "Delphi Method"
    description = (
        "Anonymous iterative forecasting.  Participants provide independent "
        "estimates, then see the group's statistical distribution and "
        "anonymised reasoning before revising.  Avoids anchoring, authority "
        "bias, and social pressure.  Best for questions with quantifiable "
        "answers or probability estimates."
    )
    phase_handlers = (
        EstimateHandler(),
        ReviseDelphiHandler(),
        SynthesiseDelphiHandler(),
    )
    assumes_independent_panel = True

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        super().on_round_complete(discussion)
        phase = self.current_phase(discussion)
        if phase and phase.name == "revise":
            discussion.method_state["revise_round"] = (
                discussion.method_state.get("revise_round", 0) + 1
            )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        summary = self._build_full_trajectory(discussion)

        body = (
            "The Delphi Method process is complete.\n\n"
            f"Estimate trajectories:\n{summary}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Final distribution** — Report the median, mean, range, "
            "and inter-quartile range of the final estimates\n"
            "2. **Convergence analysis** — Did the group converge?  How "
            "much did estimates change from initial to final round?\n"
            "3. **Outlier analysis** — Were there persistent outliers?  "
            "Did their reasoning contain unique insights or errors?\n"
            "4. **Key arguments** — Which arguments were most influential "
            "in driving convergence or maintaining divergence?\n"
            "5. **Confidence assessment** — Based on the convergence "
            "pattern and reasoning quality, how confident should we be "
            "in the group estimate?\n"
            "6. **Final answer** — State the group's best estimate with "
            "uncertainty bounds.\n\n"
            "Present actual numbers and cite specific reasoning."
        )
        disclosure = self.panel_composition_disclosure(discussion)
        return f"{disclosure}\n\n{body}" if disclosure else body

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_full_trajectory(self, discussion: Discussion) -> str:
        """Build a full trajectory of estimates across all rounds."""
        state = discussion.method_state
        estimates = state.get("estimates", [])

        if not estimates:
            return "(No data)"

        # Group by entity
        by_entity: dict[int, list[dict]] = {}
        for e in estimates:
            by_entity.setdefault(e["entity_id"], []).append(e)

        lines = []
        for eid, entries in by_entity.items():
            name = entries[0].get("entity_name", f"Entity {eid}")
            lines.append(f"  **{name}:**")
            for entry in sorted(entries, key=lambda e: e["round"]):
                label = ("Initial" if entry["round"] == 0
                         else f"Round {entry['round']}")
                val = entry.get("value", "?")
                conf = entry.get("confidence", "?")
                lines.append(f"    {label}: {val} ({conf})")

        return "\n".join(lines)
