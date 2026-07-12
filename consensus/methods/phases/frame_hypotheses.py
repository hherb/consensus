"""Frame Hypotheses phase handler for Belief Diffusion.

The moderator decomposes the question into competing hypotheses.
This is a moderator-only phase: the moderator takes the turn, states
3-5 competing hypotheses as a numbered list, and the handler parses
them into method_state.  Bounded retries prevent an unparseable
framing from looping forever.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logging

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._belief_helpers import (
    DEFAULT_CONVERGENCE_THRESHOLD,
    MAX_DIFFUSE_ROUNDS,
    extract_hypotheses_from_framing,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

# Give up on framing after this many unparseable moderator responses.
MAX_FRAMING_ATTEMPTS = 3


class FrameHypothesesHandler(PhaseHandler):
    """Phase 1: Moderator decomposes question into hypotheses."""

    phase = Phase(
        name="frame",
        display_name="Framing",
        description=(
            "The moderator will decompose the question into 3-5 competing "
            "hypotheses or possible answers for participants to evaluate."
        ),
        rounds=0,  # condition-based: hypotheses parsed or attempts exhausted
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "hypotheses": [],
            "belief_history": [],
            "convergence_threshold": DEFAULT_CONVERGENCE_THRESHOLD,
            "max_diffuse_rounds": MAX_DIFFUSE_ROUNDS,
            "diffuse_round": 0,
            "framing_attempts": 0,
        }

    # ------------------------------------------------------------------
    # Turn order — moderator only
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only the moderator speaks during framing."""
        return [discussion.moderator_id]

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator framing a Belief State Diffusion "
            "exercise.\n"
            f"Topic: {discussion.topic}\n\n"
            "Your task is to decompose this question into 3-5 competing, "
            "mutually exclusive hypotheses that together cover the "
            "plausible answer space.  Participants will then assign and "
            "iteratively update probability estimates over these "
            "hypotheses."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if state.get("framing_attempts", 0) > 0:
            return (
                "The previous framing did not contain a parseable "
                "numbered list.  Please restate the competing hypotheses "
                "as a plain NUMBERED LIST, one hypothesis per line:\n"
                "1. <first hypothesis>\n"
                "2. <second hypothesis>\n"
                "..."
            )
        return (
            "Decompose the topic into 3-5 competing hypotheses.  Each "
            "should be specific and mutually exclusive where possible.  "
            "State them as a NUMBERED LIST, one hypothesis per line:\n"
            "1. <first hypothesis>\n"
            "2. <second hypothesis>\n"
            "..."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract hypotheses from the moderator's framing response."""
        state = discussion.method_state
        hypotheses = extract_hypotheses_from_framing(content)
        if hypotheses:
            state["hypotheses"] = hypotheses
            logger.info("Extracted %d hypotheses from framing", len(hypotheses))
        else:
            state["framing_attempts"] = state.get("framing_attempts", 0) + 1
            logger.warning(
                "Framing attempt %d failed — no hypotheses found",
                state["framing_attempts"],
            )
        return ProcessedResponse(display_content=content)

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("hypotheses"):
            return True
        if state.get("framing_attempts", 0) >= MAX_FRAMING_ATTEMPTS:
            logger.warning(
                "Giving up on hypothesis framing after %d attempts",
                MAX_FRAMING_ATTEMPTS,
            )
            return True
        return False
