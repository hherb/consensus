"""Revise phase handler for Delphi Method.

After seeing the group's distribution and anonymised reasoning,
participants revise their estimates.  Condition-based advancement
(convergence or max rounds).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str
from ..phase_handler import PhaseHandler
from ._delphi_helpers import (
    ESTIMATE_TOOL_PARAMETERS,
    MAX_REVISE_ROUNDS,
    anonymise_content,
    build_distribution_summary,
    check_convergence,
    extract_estimate,
    format_estimate_bar,
    record_estimate,
    validate_estimate_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ReviseDelphiHandler(PhaseHandler):
    """Phase 2: Revision rounds (condition-based advancement)."""

    phase = Phase(
        name="revise",
        display_name="Revision Rounds",
        description=(
            "After seeing the group's statistical distribution and "
            "anonymised reasoning, participants revise their estimates."
        ),
        rounds=0,  # condition-based (convergence)
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        summary = build_distribution_summary(discussion)
        round_num = state.get("revise_round", 0) + 1

        base = (
            f"You are {entity.name}, participating in a Delphi Method "
            f"forecasting exercise.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        return base + (
            f"REVISION ROUND {round_num}\n\n"
            f"Here is the group's distribution from the previous "
            f"round (anonymised):\n{summary}\n\n"
            "Review the distribution and anonymised reasoning.  Then "
            "provide your REVISED estimate by calling the "
            "submit_estimate tool.\n\n"
            "In the 'reasoning' field, explain:\n"
            "1. Has your estimate changed?  By how much and why?\n"
            "2. Which anonymised arguments were most persuasive?\n"
            "3. What reasoning do you maintain despite the group "
            "distribution?\n\n"
            "You are NOT obligated to move toward the group — only "
            "revise if the reasoning warrants it."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("revise_round", 0) + 1
        return (
            f"Revision round {round_num}, {entity.name}.  Review the "
            "group distribution and provide your revised estimate by "
            "calling the submit_estimate tool.  In the 'reasoning' "
            "field, explain how and why your estimate has or hasn't "
            "changed."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"A revised estimate has been received.  Do NOT reveal "
            f"or summarise individual estimates or reasoning — "
            f"anonymity must be preserved.  Simply invite the next "
            f"participant to provide their revised estimate.\n\n"
            f"{next_speaker_name}, please provide your revised "
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
                state, entity, state.get("revise_round", 0) + 1,
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
            description=("Submit your revised estimate after reviewing "
                         "the group distribution."),
            parameters=ESTIMATE_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_estimate_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        value = float(payload["estimate"])
        confidence = str(payload["confidence"]).upper()
        unit = coerce_str(payload, "unit")
        record_estimate(state, entity, state.get("revise_round", 0) + 1,
                        value, confidence, unit)
        display = (str(payload["reasoning"]).strip()
                   + format_estimate_bar(value, confidence, unit))
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        revise_round = state.get("revise_round", 0)
        max_rounds = state.get("max_revise_rounds", MAX_REVISE_ROUNDS)
        if revise_round >= max_rounds:
            return True
        return check_convergence(discussion)

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO revise)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        summary = build_distribution_summary(discussion)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All initial estimates are in.  Here is the anonymised "
            f"distribution:\n{summary}\n\n"
            "Each participant will now revise their estimate after "
            "seeing the group's distribution and anonymised reasoning."
        )
