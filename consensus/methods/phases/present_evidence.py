"""Present Evidence phase handler for Adversarial Collaboration.

Participants gather evidence relevant to the agreed settlement criteria.
Tools are enabled for searching facts, data, and studies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class PresentEvidenceHandler(PhaseHandler):
    """Phase 3: Gather evidence against locked criteria."""

    phase = Phase(
        name="evidence",
        display_name="Evidence Gathering",
        description=(
            "Gather evidence relevant to the agreed criteria.  "
            "Focus specifically on the criteria — not on general "
            "arguments for your position."
        ),
        rounds=2,
        allow_tools=True,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        base = (
            f"You are {entity.name}, participating in an Adversarial "
            f"Collaboration.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        criteria = state.get("criteria", [])
        criteria_text = "\n".join(
            f"  C{i+1}: {c}" for i, c in enumerate(criteria)
        )
        return base + (
            "EVIDENCE GATHERING PHASE\n\n"
            f"Agreed settlement criteria:\n{criteria_text}\n\n"
            "Find evidence specifically relevant to these criteria.  "
            "Use tools to search for facts, data, and studies.\n\n"
            "For each piece of evidence:\n"
            "1. State the evidence clearly\n"
            "2. Cite the source\n"
            "3. Indicate which criterion it addresses (C1, C2, etc.)\n"
            "4. State what it supports\n\n"
            "Be honest — report evidence even if it undermines your "
            "position.  The criteria were designed to be fair."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("phase_round", 1)
        return (
            f"Evidence round {round_num}, {entity.name}.  Find and "
            "present evidence relevant to the agreed criteria.  "
            "Use your tools."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented evidence.  Note which "
            "criteria it addresses and which position it supports.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        criteria = state.get("criteria", [])
        criteria_text = "\n".join(
            f"  **C{i+1}:** {c}" for i, c in enumerate(criteria)
        )
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Settlement criteria are now LOCKED:\n"
            f"{criteria_text}\n\n"
            "Participants will gather evidence relevant to these "
            "criteria.  No new criteria may be introduced."
        )
