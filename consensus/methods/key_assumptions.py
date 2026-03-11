"""Key Assumptions Check — surface and challenge hidden assumptions.

Before analysis begins, participants explicitly identify the assumptions
underlying the question or prevailing view, then systematically challenge
each one.  Can function as a standalone method or as a mandatory first
phase in other methods.

Phases:
  1. SURFACE    — Each participant identifies key assumptions
  2. CHALLENGE  — Each participant challenges the surfaced assumptions
  3. ASSESS     — Moderator synthesises which assumptions hold, which
                   are vulnerable, and how this affects the analysis
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases import (
    AssessAssumptionsHandler,
    ChallengeAssumptionsHandler,
    SurfaceAssumptionsHandler,
)

if TYPE_CHECKING:
    from ..models import Discussion


class KeyAssumptionsCheck(DiscussionMethod):
    """Key Assumptions Check — expose and test hidden assumptions."""

    name = "key_assumptions"
    display_name = "Key Assumptions Check"
    description = (
        "Explicitly surface the assumptions underlying the question or "
        "prevailing view, then systematically challenge each one.  "
        "Prevents analysis from being built on unexamined foundations.  "
        "Effective as a standalone method or as a first phase before "
        "deeper analysis."
    )
    phase_handlers = (
        SurfaceAssumptionsHandler(),
        ChallengeAssumptionsHandler(),
        AssessAssumptionsHandler(),
    )

    # ------------------------------------------------------------------
    # Cross-phase: conclusion prompt
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        assumptions = state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
        )

        return (
            "The Key Assumptions Check is complete.\n\n"
            f"Assumptions examined:\n{assumption_list}\n\n"
            "Provide a comprehensive assessment:\n"
            "1. **Assumption status** — Classify each assumption as:\n"
            "   - CONFIRMED (strong evidence, high confidence)\n"
            "   - CONTESTED (mixed evidence, disagreement among participants)\n"
            "   - UNSUPPORTED (weak evidence, low confidence)\n"
            "   - REFUTED (strong counter-evidence)\n"
            "2. **Load-bearing assumptions** — Which assumptions, if wrong, "
            "would most change the overall analysis or conclusion?\n"
            "3. **Blind spots** — Were any important assumptions NOT surfaced "
            "that should have been?\n"
            "4. **Recommendations** — Given the assumption landscape, what "
            "should be investigated further before proceeding?  What "
            "conclusions should be held tentatively?\n\n"
            "Be specific and cite the challenges raised by participants."
        )
