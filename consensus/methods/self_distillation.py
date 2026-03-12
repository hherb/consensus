"""Recursive Self-Distillation — separate persuasiveness from validity.

LLM-native method that generates rich reasoning, strips it to a
pure logical skeleton (premises → inferences → conclusions), then
blind-evaluates only the skeleton. Reveals where rhetorical force
substitutes for sound logic.

Phases:
  1. SD_DELIBERATE   — Rich discussion encouraging expressive reasoning
  2. DISTILL         — Moderator extracts bare logical skeleton
  3. BLIND_EVALUATE  — Participants evaluate skeleton without original context
  4. SD_SYNTHESIZE   — Moderator compares persuasiveness with validity
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.blind_evaluate import BlindEvaluateHandler
from .phases.deliberate_distillation import DistillationDeliberateHandler
from .phases.distill_skeleton import DistillSkeletonHandler
from .phases.synthesize_distillation import SynthesizeDistillationHandler
from .phases._distillation_helpers import format_validity_table

if TYPE_CHECKING:
    from ..models import Discussion

logger = logging.getLogger(__name__)


class RecursiveSelfDistillation(DiscussionMethod):
    """Recursive Self-Distillation — test argument validity vs. persuasiveness."""

    name = "self_distillation"
    display_name = "Recursive Self-Distillation"
    description = (
        "Separates persuasiveness from logical validity. Participants "
        "first build the strongest possible arguments, then a moderator "
        "strips them to a bare logical skeleton. Participants blind-"
        "evaluate only the skeleton for logical soundness, revealing "
        "where rhetorical force substituted for valid reasoning."
    )
    phase_handlers = (
        DistillationDeliberateHandler(),
        DistillSkeletonHandler(),
        BlindEvaluateHandler(),
        SynthesizeDistillationHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Build the final synthesis comparing persuasiveness with validity."""
        state = discussion.method_state
        skeleton = state.get("skeleton")
        skeleton_display = state.get("skeleton_display", "")
        rich_summary = state.get("rich_reasoning_summary", "")
        validity_scores = state.get("validity_scores", {})
        overall_scores = state.get("overall_scores", {})

        if not skeleton:
            return (
                "The self-distillation process could not extract a "
                "logical skeleton from the discussion. Please provide "
                "a qualitative summary of the discussion, noting the "
                "key arguments made and your assessment of their "
                "logical strength."
            )

        table = format_validity_table(skeleton, validity_scores)

        # Format overall scores
        overall_parts: list[str] = []
        for name, score in sorted(overall_scores.items()):
            overall_parts.append(f"- {name}: {score}/5")
        overall_section = "\n".join(overall_parts) if overall_parts else "No overall scores recorded."

        return (
            "The Recursive Self-Distillation process is complete.\n\n"
            "## Original Rich Reasoning\n\n"
            f"{rich_summary or '(not captured)'}\n\n"
            "## Logical Skeleton\n\n"
            f"{skeleton_display}\n\n"
            "## Blind Validity Scores\n\n"
            f"{table}\n\n"
            "## Overall Argument Scores\n\n"
            f"{overall_section}\n\n"
            "## Your Synthesis\n\n"
            "Provide a comprehensive analysis:\n\n"
            "1. **Validity vs. Persuasiveness** — Which inference steps "
            "scored high on logical validity? Which were given the most "
            "rhetorical weight in the original discussion? Where do "
            "these diverge?\n\n"
            "2. **Rhetoric masking weakness** — Identify specific cases "
            "where persuasive language, vivid examples, or emotional "
            "appeals in the original discussion obscured a logically "
            "weak inference step.\n\n"
            "3. **Underappreciated logic** — Were any logically SOUND "
            "steps under-emphasized or taken for granted in the "
            "original discussion?\n\n"
            "4. **Distilled conclusion** — State the conclusion that "
            "is supported ONLY by the logically sound inference steps "
            "(those rated SOUND). How does this differ from the "
            "original discussion's conclusion?\n\n"
            "5. **Confidence assessment** — Given the validity analysis, "
            "how confident should we be in the conclusion? What are "
            "the remaining logical vulnerabilities?\n\n"
            "Be specific — cite inference IDs, validity scores, and "
            "contrast with the original rhetoric."
        )
