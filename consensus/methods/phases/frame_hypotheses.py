"""Frame Hypotheses phase handler for Belief Diffusion.

The moderator decomposes the question into competing hypotheses.
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


class FrameHypothesesHandler(PhaseHandler):
    """Phase 1: Moderator decomposes question into hypotheses."""

    phase = Phase(
        name="frame",
        display_name="Framing",
        description=(
            "The moderator will decompose the question into 3-5 competing "
            "hypotheses or possible answers for participants to evaluate."
        ),
        rounds=1,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "hypotheses": [],
            "belief_history": [],
            "convergence_threshold": DEFAULT_CONVERGENCE_THRESHOLD,
            "max_diffuse_rounds": MAX_DIFFUSE_ROUNDS,
            "diffuse_round": 0,
        }

    # Moderator-only phase — prompts are empty because the moderator
    # uses its own framing logic; participants do not speak.

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract hypotheses from the moderator's framing response."""
        hypotheses = extract_hypotheses_from_framing(content)
        if hypotheses:
            discussion.method_state["hypotheses"] = hypotheses
            logger.info("Extracted %d hypotheses from framing", len(hypotheses))
        return ProcessedResponse(display_content=content)

    def should_advance(self, discussion: Discussion) -> bool:
        return bool(discussion.method_state.get("hypotheses"))
