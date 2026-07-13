"""Extract Claims phase handler for Counterfactual Stress Testing.

Moderator extracts 3-7 key falsifiable claims from the deliberation
or prior conclusion. Includes retry logic for failed extractions.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

MAX_EXTRACTION_ATTEMPTS = 3

# Captures the "CONCLUSION: ..." statement at the top of the moderator's
# extraction response, up to the first numbered claim or end of text.
# The moderator summary path never reaches process_response, so the
# preliminary conclusion is captured here, in the moderator-only extract
# turn (issue #15).
_CONCLUSION_RE = re.compile(
    r"^\s*\**CONCLUSION:?\**\s*:?\s*(.+?)(?=\n\s*\d+[\.\)]|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)

#: Minimum length (inclusive) a claim string must reach to count as
#: substantive.  Matches ``parse_numbered_list``'s default
#: ``min_length=10`` so the free-text and structured-output paths hold
#: claims to the same bar.
CLAIM_MIN_LENGTH = 10

#: JSON Schema for the submit_claims output tool (issue #23).
#: ``preliminary_conclusion`` replaces the free-text ``CONCLUSION:``
#: line the moderator used to prefix the numbered claim list with.
CLAIMS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "3-7 key claims the conclusion depends on. Each must be "
                "a specific, falsifiable assertion -- not a value "
                "judgment or vague statement."
            ),
        },
        "preliminary_conclusion": {
            "type": "string",
            "description": (
                "The discussion's preliminary conclusion in one "
                "paragraph -- synthesise it if not already given."
            ),
        },
    },
    "required": ["claims", "preliminary_conclusion"],
}


def validate_claims_payload(payload: dict) -> str:
    """Return '' if a submit_claims payload is usable, else an error.

    Mirrors the free-text path's behaviour: ``claims`` must be a
    non-empty array and at least one entry must be substantive (at
    least ``CLAIM_MIN_LENGTH`` characters after stripping) -- exactly
    like ``parse_numbered_list`` silently drops short items but only
    fails when nothing survives.  ``preliminary_conclusion`` must be
    non-empty prose, the structured replacement for the free-text
    ``CONCLUSION:`` line.
    """
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        return "'claims' must be a non-empty array of claim strings."
    if not any(isinstance(c, str) and len(c.strip()) >= CLAIM_MIN_LENGTH
               for c in claims):
        return (
            "At least one claim must be a substantive string of "
            f"{CLAIM_MIN_LENGTH}+ characters describing a specific, "
            "falsifiable assertion."
        )
    if not str(payload.get("preliminary_conclusion", "")).strip():
        return ("'preliminary_conclusion' must contain the discussion's "
                "preliminary conclusion in one paragraph.")
    return ""


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
            "that the conclusion depends on.\n\n"
            "Submit your extraction by calling the submit_claims tool."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = (state.get("preliminary_conclusion")
                      or state.get("prior_conclusion")
                      or "(no conclusion available)")

        has_conclusion = bool(state.get("preliminary_conclusion")
                              or state.get("prior_conclusion"))
        conclusion_instruction = (
            ""
            if has_conclusion else
            "First, synthesise the discussion's preliminary conclusion "
            "in one paragraph for the preliminary_conclusion field.  "
            "Then "
        )

        if state.get("extraction_failed") and state.get("extraction_attempts", 0) > 0:
            return (
                "The previous extraction did not produce a usable "
                "result. Please try again.\n\n"
                f"Conclusion to analyze:\n{conclusion}\n\n"
                f"{conclusion_instruction}extract 3-7 key claims. Each "
                "claim must be a specific, falsifiable assertion — not "
                "a value judgment or vague statement. Call the "
                "submit_claims tool with a claims array (3-7 entries) "
                "and the preliminary_conclusion field."
            )

        return (
            "Review the discussion above and the conclusion reached.\n\n"
            f"Conclusion:\n{conclusion}\n\n"
            f"{conclusion_instruction}extract 3-7 key claims that this "
            "conclusion depends on. "
            "Each claim should be a specific, falsifiable assertion — "
            "not a value judgment or vague statement. Submit them by "
            "calling the submit_claims tool with a claims array (3-7 "
            "entries) and the preliminary_conclusion field."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state

        # Capture the preliminary conclusion stated by the moderator,
        # unless one was already provided (prior_conclusion) or captured.
        if not (state.get("preliminary_conclusion")
                or state.get("prior_conclusion")):
            m = _CONCLUSION_RE.search(content)
            if m:
                state["preliminary_conclusion"] = m.group(1).strip()

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
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_claims tool for this phase.

        Like distill_skeleton's submit_skeleton, there is no state that
        makes the schema unsatisfiable (extraction always has a
        discussion/conclusion to draw claims from), so this always
        returns a spec.
        """
        return OutputToolSpec(
            name="submit_claims",
            description=(
                "Submit 3-7 key falsifiable claims the conclusion "
                "depends on, plus the discussion's preliminary "
                "conclusion in one paragraph."
            ),
            parameters=CLAIMS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_claims payload via the shared function."""
        return validate_claims_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Store the submitted claims and render the same display.

        Mirrors ``process_response``'s successful branch: filters out
        non-substantive claims (``CLAIM_MIN_LENGTH``, matching
        ``parse_numbered_list``'s filtering), builds the same
        ``claims``/``claim_results`` structures, clears
        ``extraction_failed``, and captures
        ``preliminary_conclusion`` from the payload -- once only,
        respecting the same ``prior_conclusion`` precedence the
        free-text ``_CONCLUSION_RE`` capture in ``process_response``
        honours.
        """
        state = discussion.method_state

        substantive = [
            text.strip() for text in payload["claims"]
            if isinstance(text, str) and len(text.strip()) >= CLAIM_MIN_LENGTH
        ]
        claims = [{"id": i + 1, "text": text}
                  for i, text in enumerate(substantive)]
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

        conclusion = str(payload.get("preliminary_conclusion", "")).strip()
        if not (state.get("preliminary_conclusion")
                or state.get("prior_conclusion")):
            state["preliminary_conclusion"] = conclusion

        logger.info(
            "Extracted %d claims via submit_claims for stress testing",
            len(claims),
        )

        numbered = "\n".join(f"{c['id']}. {c['text']}" for c in claims)
        display = f"**Preliminary conclusion:** {conclusion}\n\n{numbered}"
        return ProcessedResponse(display_content=display)

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("claims"):
            return True
        if state.get("extraction_attempts", 0) >= MAX_EXTRACTION_ATTEMPTS:
            logger.warning("Giving up on claim extraction after %d attempts",
                           MAX_EXTRACTION_ATTEMPTS)
            return True
        return False
