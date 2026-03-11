"""Extract Claims phase handler for Counterfactual Stress Testing.

Moderator extracts 3-7 key falsifiable claims from the deliberation
or prior conclusion. Includes retry logic for failed extractions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

MAX_EXTRACTION_ATTEMPTS = 3


class ExtractClaimsHandler(PhaseHandler):
    """Phase 2: Moderator extracts key falsifiable claims."""

    phase = Phase(
        name="extract",
        display_name="Claim Extraction",
        description=(
            "The moderator extracts 3-7 key falsifiable claims that "
            "the conclusion depends on."
        ),
        rounds=0,
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "claims": [],
            "claim_results": [],
            "current_claim_index": 0,
            "extraction_failed": False,
            "extraction_attempts": 0,
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator extracting testable claims from "
            "the discussion. Identify the specific, falsifiable assertions "
            "that the conclusion depends on."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = (state.get("preliminary_conclusion")
                      or state.get("prior_conclusion")
                      or "(no conclusion available)")

        if state.get("extraction_failed") and state.get("extraction_attempts", 0) > 0:
            return (
                "The previous extraction failed to produce a numbered list "
                "of claims. Please try again.\n\n"
                f"Conclusion to analyze:\n{conclusion}\n\n"
                "Extract 3-7 key claims as a NUMBERED LIST. Each claim "
                "must be a specific, falsifiable assertion — not a value "
                "judgment or vague statement. Use this format:\n"
                "1. <claim>\n"
                "2. <claim>\n"
                "..."
            )

        return (
            "Review the discussion above and the conclusion reached.\n\n"
            f"Conclusion:\n{conclusion}\n\n"
            "Extract 3-7 key claims that this conclusion depends on. "
            "Each claim should be a specific, falsifiable assertion — "
            "not a value judgment or vague statement. List them as a "
            "numbered list:\n"
            "1. <claim>\n"
            "2. <claim>\n"
            "..."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        parsed = parse_numbered_list(content)

        if not parsed:
            state["extraction_failed"] = True
            state["extraction_attempts"] = state.get("extraction_attempts", 0) + 1
            logger.warning(
                "Claim extraction attempt %d failed — no numbered items found",
                state["extraction_attempts"],
            )
            return ProcessedResponse(display_content=content)

        claims = [{"id": i + 1, "text": text} for i, text in enumerate(parsed)]
        state["claims"] = claims
        state["extraction_failed"] = False

        state["claim_results"] = [
            {
                "claim_id": c["id"],
                "claim_text": c["text"],
                "scores": {},
                "avg_score": None,
                "classification": None,
            }
            for c in claims
        ]

        logger.info("Extracted %d claims for stress testing", len(claims))
        return ProcessedResponse(
            display_content=content,
            extracted_data={"claims": claims},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("claims"):
            return True
        if state.get("extraction_attempts", 0) >= MAX_EXTRACTION_ATTEMPTS:
            logger.warning("Giving up on claim extraction after %d attempts",
                           MAX_EXTRACTION_ATTEMPTS)
            return True
        return False
