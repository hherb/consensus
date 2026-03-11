"""Revise phase handler for Delphi Method.

After seeing the group's distribution and anonymised reasoning,
participants revise their estimates.  Condition-based advancement
(convergence or max rounds).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import (
    MAX_REVISE_ROUNDS,
    anonymise_content,
    build_distribution_summary,
    check_convergence,
    extract_estimate,
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
            "provide your REVISED estimate.\n\n"
            "You MUST include a JSON block:\n"
            "```json\n"
            '{"estimate": <number_or_probability>, '
            '"confidence": "<HIGH/MEDIUM/LOW>", '
            '"unit": "<what the number represents>"}\n'
            "```\n\n"
            "Explain:\n"
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
            "group distribution and provide your revised estimate.\n\n"
            "CRITICAL: Your response MUST start with a JSON code block "
            "containing your revised estimate.  Place it as the VERY "
            "FIRST thing in your response, before any other text:\n"
            "```json\n"
            '{"estimate": <number>, "confidence": "<HIGH/MEDIUM/LOW>", '
            '"unit": "<what the number represents>"}\n'
            "```\n"
            "Then explain how and why your estimate has or hasn't changed."
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
            round_num = state.get("revise_round", 0) + 1
            entry = {
                "round": round_num,
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
