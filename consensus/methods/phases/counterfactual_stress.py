"""Stress Test phase handler for Counterfactual Stress Testing.

For each claim, all participants argue from the premise that it is
false and score the impact on the overall conclusion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._counterfactual_helpers import extract_impact_score

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class StressTestHandler(PhaseHandler):
    """Phase 3: Systematically invert each claim and assess impact."""

    phase = Phase(
        name="stress_test",
        display_name="Counterfactual Stress Test",
        description=(
            "Each key claim is systematically inverted. Participants "
            "must argue from the counterfactual premise and rate "
            "the impact on the conclusion."
        ),
        rounds=0,
        allow_tools=True,
    )

    def _current_claim(self, discussion: Discussion) -> dict | None:
        """Return the claim currently under test, or None."""
        state = discussion.method_state
        idx = state.get("current_claim_index", 0)
        claims = state.get("claims", [])
        if 0 <= idx < len(claims):
            return claims[idx]
        return None

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        conclusion = (discussion.method_state.get("preliminary_conclusion")
                      or "(no conclusion)")

        return (
            f"You are {entity.name}, participating in a counterfactual "
            f"stress test.\n"
            f"Topic: {discussion.topic}\n"
            f"Preliminary conclusion: {conclusion}\n\n"
            "COUNTERFACTUAL STRESS TEST\n\n"
            f"The claim under test is: \"{claim_text}\"\n\n"
            "You MUST argue from the premise that this claim is FALSE — "
            "even if you believe it is true. Your job is to honestly "
            "assess how much damage losing this claim does to the "
            "overall conclusion."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        idx = state.get("current_claim_index", 0)
        total = len(state.get("claims", []))

        return (
            f"--- Counterfactual Test #{idx + 1} of {total} ---\n"
            f"Assume the following claim is FALSE: \"{claim_text}\"\n\n"
            f"It is your turn, {entity.name}. Given this counterfactual, "
            "how does the overall conclusion change? What breaks? What "
            "still holds?\n\n"
            "Rate the impact on a scale of 1-5 at the end of your "
            "response using this exact format:\n"
            "[IMPACT: N]\n"
            "where 1 = conclusion completely unaffected, "
            "5 = conclusion collapses entirely."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        return (
            f"{speaker_name} has assessed the impact of losing the "
            f"claim \"{claim_text}\". Briefly note their damage "
            f"assessment. Next: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state

        # Skip moderator entities — only participants score
        if entity.id == discussion.moderator_id:
            return ProcessedResponse(display_content=content)

        score = extract_impact_score(content)
        idx = state.get("current_claim_index", 0)
        claim_results = state.get("claim_results", [])
        if score is not None and idx < len(claim_results):
            claim_results[idx]["scores"][entity.name] = score

        return ProcessedResponse(
            display_content=content,
            extracted_data={"impact_score": score},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        idx = state.get("current_claim_index", 0)
        total = len(state.get("claims", []))
        return idx >= total

    def get_transition_message(self, discussion: Discussion) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        total = len(discussion.method_state.get("claims", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"We will now test {total} key claims by systematically "
            "assuming each one is false and assessing the damage to "
            "the conclusion.\n\n"
            f"**First claim under test:** \"{claim_text}\""
        )
