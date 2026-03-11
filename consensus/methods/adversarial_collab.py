"""Adversarial Collaboration (Kahneman-style) — structured disagreement.

Participants who genuinely disagree jointly design the criteria that
would settle the question BEFORE gathering evidence.  This prevents
post-hoc rationalisation and moves-the-goalposts fallacies.

Phases:
  1. POSITIONS    — Each participant states their position and reasoning
  2. CRITERIA     — Participants jointly define what evidence would
                    settle the question (ideally before seeing evidence)
  3. EVIDENCE     — Gather evidence according to the agreed criteria
  4. ADJUDICATE   — Moderator evaluates the evidence against the
                    pre-agreed criteria and declares a verdict
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases import (
    AdjudicateHandler,
    DefineCriteriaHandler,
    PresentEvidenceHandler,
    StatePositionsHandler,
)

if TYPE_CHECKING:
    from ..models import Discussion


class AdversarialCollaboration(DiscussionMethod):
    """Adversarial Collaboration — agree on criteria before evidence."""

    name = "adversarial_collab"
    display_name = "Adversarial Collaboration"
    description = (
        "Participants who disagree jointly design the criteria that "
        "would settle the question BEFORE gathering evidence.  Prevents "
        "post-hoc rationalisation and goalpost-shifting.  Inspired by "
        "Daniel Kahneman's adversarial collaboration methodology."
    )
    phase_handlers = (
        StatePositionsHandler(),
        DefineCriteriaHandler(),
        PresentEvidenceHandler(),
        AdjudicateHandler(),
    )

    # ------------------------------------------------------------------
    # Cross-phase: conclusion prompt
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        positions = state.get("positions", {})
        criteria = state.get("criteria", [])

        pos_text = "\n".join(
            f"  - {eid}: {pos}"
            for eid, pos in positions.items()
        )
        criteria_text = "\n".join(
            f"  C{i+1}: {c}" for i, c in enumerate(criteria)
        )

        return (
            "The Adversarial Collaboration is complete.\n\n"
            f"Positions:\n{pos_text}\n\n"
            f"Pre-agreed settlement criteria:\n{criteria_text}\n\n"
            "Provide a comprehensive adjudication:\n"
            "1. **Criterion-by-criterion verdict** — For each criterion, "
            "what does the evidence show?  Which position does it support?\n"
            "2. **Overall verdict** — Based on the pre-agreed criteria, "
            "which position is better supported?  By how much?\n"
            "3. **Unresolved criteria** — Were any criteria impossible to "
            "evaluate with the available evidence?\n"
            "4. **Areas of genuine uncertainty** — Where does the evidence "
            "remain ambiguous or insufficient?\n"
            "5. **Synthesis** — Is there a more nuanced position that "
            "integrates the strongest elements of both sides?\n\n"
            "You MUST evaluate against the pre-agreed criteria, not "
            "introduce new ones.  The whole point of this method is that "
            "criteria were locked before evidence was gathered."
        )
