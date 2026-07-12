"""Estimate phase handler for Delphi Method.

Each participant provides an independent estimate with reasoning.
Names are anonymised in context messages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import (
    DEFAULT_CONVERGENCE_RATIO,
    ESTIMATE_TOOL_PARAMETERS,
    MAX_REVISE_ROUNDS,
    anonymise_content,
    extract_estimate,
    format_estimate_bar,
    record_estimate,
    validate_estimate_payload,
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
            "Submit your estimate by calling the submit_estimate tool "
            "with your estimate, confidence (HIGH/MEDIUM/LOW), unit, "
            "and detailed reasoning covering:\n"
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
            f"It is your turn, {entity.name}.  Provide your independent "
            "estimate by calling the submit_estimate tool.  Put your "
            "full detailed reasoning in the 'reasoning' field."
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
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        estimate_data = extract_estimate(content)

        if estimate_data:
            record_estimate(
                state, entity, 0,
                estimate_data.get("estimate"),
                estimate_data.get("confidence", ""),
                estimate_data.get("unit", ""),
            )
            display = content + format_estimate_bar(
                estimate_data.get("estimate", "?"),
                estimate_data.get("confidence", "?"),
                estimate_data.get("unit", ""),
            )
        else:
            logger.warning(
                "Could not extract estimate from %s's response",
                entity.name,
            )
            display = content

        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_estimate",
            description=("Submit your independent estimate with "
                         "detailed reasoning."),
            parameters=ESTIMATE_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_estimate_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        value = float(payload["estimate"])
        confidence = str(payload["confidence"]).upper()
        unit = str(payload.get("unit", ""))
        record_estimate(discussion.method_state, entity, 0,
                        value, confidence, unit)
        display = (str(payload["reasoning"]).strip()
                   + format_estimate_bar(value, confidence, unit))
        return ProcessedResponse(display_content=display)

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
