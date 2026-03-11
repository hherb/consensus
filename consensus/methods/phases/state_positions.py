"""State Positions phase handler for Adversarial Collaboration.

Each participant states their position on the question and their
strongest reasons for holding it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class StatePositionsHandler(PhaseHandler):
    """Phase 1: Each participant states their position."""

    phase = Phase(
        name="positions",
        display_name="State Positions",
        description=(
            "Each participant states their position on the question "
            "and their strongest reasons for holding it."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"positions": {}}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        base = (
            f"You are {entity.name}, participating in an Adversarial "
            f"Collaboration.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        return base + (
            "POSITION STATEMENT PHASE\n\n"
            "State your position on the question clearly and concisely.  "
            "Include:\n"
            "1. Your position (what you believe to be true)\n"
            "2. Your 2-3 strongest reasons\n"
            "3. What you think the opposing view gets wrong\n\n"
            "Be honest and direct — the goal is to make genuine "
            "disagreements explicit so they can be resolved."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  State your position "
            "on this question clearly, with your strongest reasons."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated their position.  Note the "
            "key points of agreement and disagreement with previous "
            f"positions.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        summary = content.strip()[:200]
        if len(content.strip()) > 200:
            summary += "..."
        state.setdefault("positions", {})[entity.name] = summary
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("positions"))
                and state.get("phase_round", 1) > 1)
