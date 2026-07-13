"""Challenge Assumptions phase handler for Key Assumptions Check.

Participants systematically challenge each surfaced assumption with
evidence, falsification conditions, consequences, and confidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ChallengeAssumptionsHandler(PhaseHandler):
    """Phase 2: Challenge surfaced assumptions."""

    phase = Phase(
        name="challenge",
        display_name="Challenge Assumptions",
        description=(
            "Systematically challenge each surfaced assumption.  "
            "For each, ask: What evidence supports it?  Under what "
            "conditions would it be false?  What are the consequences "
            "if it is wrong?"
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        assumptions = state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
        )
        base = (
            f"You are {entity.name}, participating in a Key Assumptions Check.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        # Empty only via the MAX_SURFACE_ROUNDS give-up: match the
        # transition message instead of rendering a blank list.
        if not assumptions:
            return base + (
                "ASSUMPTION CHALLENGE PHASE\n\n"
                "No assumptions were captured during the surfacing "
                "phase. State the assumptions YOU consider most "
                "critical to the topic, then challenge each one.\n\n"
                "For EACH assumption, provide:\n"
                "1. **Supporting evidence** — What evidence supports "
                "this assumption being true?\n"
                "2. **Falsification conditions** — Under what "
                "circumstances would this assumption be FALSE?\n"
                "3. **Consequences if wrong** — If this assumption is "
                "wrong, how would it change the analysis or "
                "conclusion?\n"
                "4. **Confidence rating** — Rate your confidence that "
                "this assumption holds: HIGH / MEDIUM / LOW\n\n"
                "Be rigorous — even assumptions you believe are "
                "correct deserve honest scrutiny."
            )
        return base + (
            "ASSUMPTION CHALLENGE PHASE\n\n"
            f"The following assumptions have been surfaced:\n"
            f"{assumption_list}\n\n"
            "For EACH assumption, provide:\n"
            "1. **Supporting evidence** — What evidence supports this "
            "assumption being true?\n"
            "2. **Falsification conditions** — Under what circumstances "
            "would this assumption be FALSE?\n"
            "3. **Consequences if wrong** — If this assumption is wrong, "
            "how would it change the analysis or conclusion?\n"
            "4. **Confidence rating** — Rate your confidence that this "
            "assumption holds: HIGH / MEDIUM / LOW\n\n"
            "Be rigorous — even assumptions you believe are correct "
            "deserve honest scrutiny."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        if not discussion.method_state.get("assumptions"):
            return (
                f"It is your turn, {entity.name}.  State and challenge "
                "the assumptions you consider most critical to the "
                "topic, with evidence, falsification conditions, "
                "consequences, and a confidence rating for each."
            )
        return (
            f"It is your turn, {entity.name}.  Systematically challenge "
            "each surfaced assumption with evidence, falsification "
            "conditions, consequences, and a confidence rating."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has challenged the assumptions.  Note "
            "any assumptions rated LOW confidence and any surprising "
            f"findings.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        assumptions = state.get("assumptions", [])
        if not assumptions:
            # Reached only via the MAX_SURFACE_ROUNDS give-up: the
            # surfacing phase collected nothing usable, and announcing
            # "0 assumptions have been surfaced:" would be baffling.
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "No assumptions were captured during the surfacing "
                "phase (its round limit was reached). Each participant "
                "should now state and immediately challenge the "
                "assumptions they consider most critical to the topic."
            )
        assumption_list = "\n".join(
            f"  **A{i+1}:** {a}" for i, a in enumerate(assumptions)
        )
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"{len(assumptions)} assumptions have been surfaced:\n"
            f"{assumption_list}\n\n"
            "Each participant will now systematically challenge these "
            "assumptions."
        )
