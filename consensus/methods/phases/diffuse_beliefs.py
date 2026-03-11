"""Diffuse Beliefs phase handler for Belief Diffusion.

Iterative belief updating: participants see others' distributions
and reasoning, then update their own.  Continues until convergence
or the round limit is reached.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._belief_helpers import (
    MAX_DIFFUSE_ROUNDS,
    check_convergence,
    extract_beliefs,
    format_belief_bar,
    format_others_beliefs,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class DiffuseBeliefsHandler(PhaseHandler):
    """Phase 3: Iterative belief updating with convergence detection."""

    phase = Phase(
        name="diffuse",
        display_name="Belief Diffusion",
        description=(
            "Participants see each other's belief distributions and reasoning, "
            "then update their own.  Continues until beliefs converge or the "
            "round limit is reached."
        ),
        rounds=0,  # condition-based
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        others_text = format_others_beliefs(entity, discussion)
        return (
            f"You are {entity.name}, a participant in a structured Belief "
            f"State Diffusion analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Other participants' current beliefs:\n{others_text}\n\n"
            "Review the other participants' reasoning carefully.  Then "
            "provide your UPDATED probability distribution.\n\n"
            "You MUST include this JSON block:\n"
            "```json\n"
            '{"beliefs": {"H1": 0.XX, "H2": 0.XX, ...}}\n'
            "```\n"
            "Probabilities must sum to 1.0.\n\n"
            "After the JSON, explain:\n"
            "1. What changed in your beliefs and WHY\n"
            "2. Which argument(s) from others were most persuasive\n"
            "3. What concerns remain\n\n"
            "If nothing changed, explain why the new arguments didn't "
            "alter your assessment."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("diffuse_round", 0) + 1
        return (
            f"Diffusion round {round_num}.  It is your turn, {entity.name}.  "
            "Review others' beliefs and reasoning, then provide your "
            "updated probability distribution with explanation."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        round_num = discussion.method_state.get("diffuse_round", 0)
        return (
            f"Diffusion round {round_num}: {speaker_name} has updated "
            "their beliefs.  Briefly note the direction and magnitude "
            "of their shift, and what drove it.  "
            f"Next up: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        beliefs = extract_beliefs(content)

        if beliefs:
            round_num = state.get("diffuse_round", 0) + 1
            entry = {
                "round": round_num,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "beliefs": beliefs,
            }
            state.setdefault("belief_history", []).append(entry)

            belief_bar = format_belief_bar(beliefs, discussion)
            display = f"{content}\n\n---\n{belief_bar}"
        else:
            logger.warning(
                "Could not extract beliefs from %s's response in diffuse phase",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data={"beliefs": beliefs} if beliefs else {},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        diffuse_round = state.get("diffuse_round", 0)
        max_rounds = state.get("max_diffuse_rounds", MAX_DIFFUSE_ROUNDS)
        if diffuse_round >= max_rounds:
            return True
        return check_convergence(discussion)

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All initial beliefs are in.  Each round, you will see "
            "other participants' beliefs and reasoning, then update "
            "your own distribution.  The process continues until beliefs "
            "converge or the round limit is reached."
        )
