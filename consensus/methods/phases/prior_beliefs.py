"""Prior Beliefs phase handler for Belief Diffusion.

Each participant states their initial probability distribution
over the hypotheses, with brief reasoning.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._belief_helpers import extract_beliefs, format_belief_bar

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class PriorBeliefsHandler(PhaseHandler):
    """Phase 2: Each participant states initial belief distribution."""

    phase = Phase(
        name="prior",
        display_name="Prior Beliefs",
        description=(
            "Each participant states their initial probability distribution "
            "over the hypotheses, with brief reasoning."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        hyp_list = "\n".join(f"  {i+1}. {h}"
                             for i, h in enumerate(hypotheses))
        return (
            f"You are {entity.name}, a participant in a structured Belief "
            f"State Diffusion analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "The following hypotheses have been identified:\n"
            f"{hyp_list}\n\n"
            "You must provide your INITIAL probability distribution over "
            "these hypotheses.  Your probabilities must sum to 1.0.\n\n"
            "Output format — you MUST include this JSON block:\n"
            "```json\n"
            '{"beliefs": {"H1": 0.XX, "H2": 0.XX, ...}}\n'
            "```\n"
            "Use the hypothesis labels H1, H2, etc.\n"
            "Follow the JSON with 2-3 sentences of reasoning."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  State your initial beliefs "
            "as a probability distribution over the hypotheses.  "
            "Include the JSON block and your reasoning."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated their initial beliefs.  "
            "Briefly note their position and any notable aspects "
            f"of their reasoning.  Next up: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        beliefs = extract_beliefs(content)

        if beliefs:
            entry = {
                "round": 0,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "beliefs": beliefs,
            }
            state.setdefault("belief_history", []).append(entry)

            belief_bar = format_belief_bar(beliefs, discussion)
            display = f"{content}\n\n---\n{belief_bar}"
        else:
            logger.warning(
                "Could not extract beliefs from %s's response in prior phase",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data={"beliefs": beliefs} if beliefs else {},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    def get_transition_message(self, discussion: Discussion) -> str:
        hypotheses = discussion.method_state.get("hypotheses", [])
        if not hypotheses:
            # Framing gave up without a parseable hypothesis list — say so
            # instead of announcing an empty list (golden rule: caught
            # errors must be shown to the user).
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "⚠️ **Warning:** The framing phase could not produce a "
                "parseable hypothesis list, so this Belief Diffusion run "
                "has no structured hypothesis set.  Belief tracking and "
                "convergence detection will be unavailable.  Consider "
                "concluding this discussion and restarting it, or switching "
                "to another method."
            )
        hyp_list = "\n".join(f"  **H{i+1}:** {h}"
                             for i, h in enumerate(hypotheses))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The following hypotheses will be evaluated:\n"
            f"{hyp_list}\n\n"
            "Each participant will now state their initial probability "
            "distribution over these hypotheses (must sum to 1.0) with "
            "brief reasoning."
        )
