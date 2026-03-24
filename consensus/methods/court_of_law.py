"""Court of Law — structured adversarial trial simulation.

Supports two modes:
  - **Criminal trial:** Prosecutor (single entity) vs Defence team
  - **Civil proceeding:** Plaintiff team vs Defence team

The mode is inferred from ``member_roles``: if any entity has role
``"prosecutor"`` it's criminal; if any has ``"plaintiff"`` it's civil.

Phases:
  1. ARRAIGNMENT      — charges / claims formally stated, defence responds
  2. OPENING STATEMENTS — each side presents theory (with team huddles)
  3. PROSECUTION CASE — accusation presents evidence, defence cross-examines
  4. DEFENCE CASE     — defence presents evidence, accusation cross-examines
  5. CLOSING ARGUMENTS — each side summarises (with team huddles)

The moderator acts as judge throughout and delivers a verdict via
``get_conclusion_prompt()`` once all phases are complete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases import (
    ArraignmentHandler,
    ClosingArgumentsHandler,
    DefenseCaseHandler,
    OpeningStatementsHandler,
    ProsecutionCaseHandler,
)
from .phases._court_helpers import (
    filter_huddle_message, get_accusation_label, get_trial_type,
)

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class CourtOfLaw(DiscussionMethod):
    """Structured adversarial trial — criminal or civil."""

    name = "court_of_law"
    display_name = "Court of Law"
    description = (
        "A structured adversarial trial.  Participants are assigned to "
        "opposing legal teams (prosecution/plaintiff vs defence).  The "
        "moderator acts as judge, controlling proceedings through "
        "arraignment, opening statements, case presentation with "
        "cross-examination, closing arguments, and a reasoned verdict.  "
        "Teams with multiple members privately huddle before speaking."
    )
    phase_handlers = (
        ArraignmentHandler(),
        OpeningStatementsHandler(),
        ProsecutionCaseHandler(),
        DefenseCaseHandler(),
        ClosingArgumentsHandler(),
    )

    # ── State initialisation ──────────────────────────────────────────

    def init_state(self, discussion: Discussion) -> dict:
        # Base state includes current_phase, phase_round, and handler states
        state = super().init_state(discussion)

        # Infer trial type from assigned roles
        roles = discussion.member_roles
        if any(r == "prosecutor" for r in roles.values()):
            trial_type = "criminal"
        elif any(r == "plaintiff" for r in roles.values()):
            trial_type = "civil"
        else:
            trial_type = "criminal"  # default fallback

        state.update({
            "trial_type": trial_type,
            "charges": [],
            "evidence_log": [],
            "objections": [],
        })
        return state

    # ── Context filtering (phase-agnostic huddle privacy) ──────────────

    def filter_context_message(self, entity_name: str, content: str,
                               role: str, discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        # Always apply huddle privacy first — regardless of active phase
        filtered = filter_huddle_message(
            entity_name, content, discussion,
            current_entity_id=current_entity_id)
        if not filtered:
            return filtered
        # Then delegate to the active handler for any phase-specific filtering
        return super().filter_context_message(
            entity_name, filtered, role, discussion,
            current_entity_id=current_entity_id)

    # ── Verdict ───────────────────────────────────────────────────────

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        trial = get_trial_type(discussion)
        trial_label = ("criminal trial" if trial == "criminal"
                       else "civil proceeding")

        return (
            f"The {trial_label} is now complete.  As the presiding judge, "
            "deliver your verdict.\n\n"
            "Structure your ruling as follows:\n\n"
            "1. **Summary of the case** — Briefly restate what was alleged "
            f"by the {label} and the Defence's position.\n\n"
            "2. **Findings on each charge/claim** — For each charge or "
            "claim raised during arraignment:\n"
            "   - State the charge/claim\n"
            "   - Summarise the key evidence and arguments from both sides\n"
            "   - Note any significant cross-examination outcomes\n"
            "   - Deliver your finding (upheld/dismissed and why)\n\n"
            "3. **Assessment of arguments** — Which side presented a more "
            "compelling case overall?  Were there notable strengths or "
            "weaknesses in either side's approach?\n\n"
            "4. **Verdict** — Deliver a clear, unambiguous ruling.  "
            "State whether the charges/claims are UPHELD or DISMISSED, "
            "individually and overall.\n\n"
            "5. **Reasoning** — Explain the legal and logical reasoning "
            "behind your verdict.  Reference specific evidence and "
            "arguments that were decisive.\n\n"
            "Be thorough, impartial, and decisive.  A good verdict "
            "acknowledges the strongest points from both sides before "
            "reaching its conclusion."
        )
