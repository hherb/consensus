"""Premortem Analysis — prospective hindsight method.

Assume a preliminary conclusion has been reached, then each participant
independently constructs a narrative of how and why it failed.  This
exploits the psychological finding that it is easier to explain a known
outcome than to critique a live idea (prospective hindsight).

Phases:
  1. FRAME        — Moderator states a preliminary conclusion or plan
  2. PREMORTEM    — Each participant imagines it failed and explains why
  3. CONSOLIDATE  — Moderator synthesises failure modes, identifies
                    the most plausible and most dangerous
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.frame_premortem import FramePremortemHandler
from .phases.premortem_imagine import PremortemImagineHandler
from .phases.consolidate_premortem import ConsolidatePremortemHandler

if TYPE_CHECKING:
    from ..models import Discussion


class PremortemAnalysis(DiscussionMethod):
    """Premortem Analysis — imagine failure before it happens."""

    name = "premortem"
    display_name = "Premortem Analysis"
    description = (
        "Assume a preliminary conclusion or plan is adopted, then each "
        "participant independently constructs a narrative of how and why "
        "it failed.  Psychologically easier than critiquing a live idea, "
        "this method surfaces risks and blind spots that normal discussion "
        "misses."
    )
    phase_handlers = (
        FramePremortemHandler(),
        PremortemImagineHandler(),
        ConsolidatePremortemHandler(),
    )

    # ------------------------------------------------------------------
    # Cross-phase prompts
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = state.get("conclusion", "")

        return (
            "The premortem analysis is complete.\n\n"
            f"Original plan/conclusion: \"{conclusion}\"\n\n"
            "Provide a comprehensive consolidation:\n"
            "1. **Failure mode inventory** — List ALL distinct failure modes "
            "identified across all narratives\n"
            "2. **Plausibility ranking** — Rank failure modes by likelihood, "
            "with brief justification\n"
            "3. **Severity assessment** — Which failures would be most "
            "damaging if they occurred?\n"
            "4. **Risk matrix** — Combine plausibility × severity to "
            "identify the highest-priority risks\n"
            "5. **Mitigations** — For each high-priority risk, suggest "
            "specific preventive measures or early warning indicators\n"
            "6. **Revised recommendation** — Should the plan proceed as-is, "
            "be modified, or be abandoned? What specific changes would "
            "address the most critical risks?\n\n"
            "Ground your analysis in the specific failure narratives "
            "provided by participants."
        )
