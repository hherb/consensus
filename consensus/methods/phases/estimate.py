"""Estimate phase handler for Delphi Method.

Each participant provides an independent estimate with reasoning.
Names are anonymised in context messages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import (
    DEFAULT_CONVERGENCE_RATIO,
    MAX_REVISE_ROUNDS,
    anonymise_content,
    extract_estimate,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class EstimateHandler(PhaseHandler):
    """Phase 1: Independent initial estimates."""

    phase = Phase(
        name="estimate",
        display_name="Initial Estimates",
        description=(
            "Each participant independently provides their estimate "
            "or assessment, with detailed reasoning."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "estimates": [],
            "revise_round": 0,
            "max_revise_rounds": MAX_REVISE_ROUNDS,
            "convergence_ratio": DEFAULT_CONVERGENCE_RATIO,
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Delphi Method "
            f"forecasting exercise.\n"
            f"Topic: {discussion.topic}\n\n"
            "INITIAL ESTIMATE PHASE\n\n"
            "Provide your independent estimate or assessment.  "
            "IMPORTANT: Do not anchor on others' views — this is your "
            "independent judgement.\n\n"
            "You MUST include a JSON block with your estimate:\n"
            "```json\n"
            '{"estimate": <number_or_probability>, '
            '"confidence": "<HIGH/MEDIUM/LOW>", '
            '"unit": "<what the number represents>"}\n'
            "```\n\n"
            "After the JSON, provide detailed reasoning:\n"
            "1. What evidence or reasoning supports your estimate?\n"
            "2. What are the key uncertainties?\n"
            "3. What would make you revise significantly upward or "
            "downward?\n\n"
            "If the question is not naturally numeric, provide a "
            "probability estimate (0.0 to 1.0) for the most likely "
            "outcome."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Provide your "
            "independent estimate with reasoning.\n\n"
            "CRITICAL: Your response MUST start with a JSON code block "
            "containing your estimate.  Place it as the VERY FIRST "
            "thing in your response, before any other text:\n"
            "```json\n"
            '{"estimate": <number>, "confidence": "<HIGH/MEDIUM/LOW>", '
            '"unit": "<what the number represents>"}\n'
            "```\n"
            "Then provide your detailed reasoning below the JSON block."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"An estimate has been received.  Do NOT reveal or "
            f"discuss the content, reasoning, or identity of any "
            f"panelist — anonymity is essential to the Delphi method.  "
            f"Simply invite the next participant.\n\n"
            f"{next_speaker_name}, please present your analysis on "
            f"the same topic.  Your response should include your "
            f"reasoned assessment, key evidence or uncertainties "
            f"considered, and what might significantly revise your "
            f"estimate."
        )

    # ------------------------------------------------------------------
    # Context filtering
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        estimate_data = extract_estimate(content)

        if estimate_data:
            entry = {
                "round": 0,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "value": estimate_data.get("estimate"),
                "confidence": estimate_data.get("confidence", ""),
                "unit": estimate_data.get("unit", ""),
            }
            state.setdefault("estimates", []).append(entry)

            val = estimate_data.get("estimate", "?")
            conf = estimate_data.get("confidence", "?")
            unit = estimate_data.get("unit", "")
            bar = f"\n\n---\n**Estimate:** {val} {unit} (Confidence: {conf})"
            display = content + bar
        else:
            logger.warning(
                "Could not extract estimate from %s's response",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data=estimate_data if estimate_data else {},
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase — not used
    # since estimate is the first phase, but included for completeness)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )
