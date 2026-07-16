"""Skeleton Extraction phase handler for Recursive Self-Distillation.

The moderator strips the rich discussion down to a pure logical
skeleton: premises, inference steps, and conclusions — with all
rhetoric, examples, analogies, and emotional language removed.
Includes retry logic for failed JSON extraction.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str, extract_json_block
from ..phase_handler import PhaseHandler
from ._distillation_helpers import (
    SKELETON_TOOL_PARAMETERS,
    format_skeleton_display,
    validate_skeleton,
    validate_skeleton_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

MAX_EXTRACTION_ATTEMPTS = 3

# Captures the "RICH SUMMARY: ..." paragraph the moderator provides
# before the skeleton JSON.  The moderator summary path never reaches
# process_response, so the rich-reasoning summary is captured here, in
# the moderator-only distill turn (issue #15).
_RICH_SUMMARY_RE = re.compile(
    r"^\s*\**RICH SUMMARY:?\**\s*:?\s*(.+?)(?=\n\s*```|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


class DistillSkeletonHandler(PhaseHandler):
    """Phase 2: Moderator extracts a logical skeleton from the discussion."""

    phase = Phase(
        name="distill",
        display_name="Skeleton Extraction",
        description=(
            "The moderator extracts the pure logical structure of the "
            "discussion — premises, inferences, and conclusions — "
            "stripping away all rhetoric, examples, and persuasive language."
        ),
        rounds=0,
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "skeleton": None,
            "skeleton_display": "",
            "extraction_attempts": 0,
            "extraction_failed": False,
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator performing logical distillation. "
            "Your task is to extract the PURE LOGICAL SKELETON of the "
            "discussion — nothing more.\n\n"
            "Strip away ALL:\n"
            "- Rhetoric and persuasive language\n"
            "- Examples and anecdotes\n"
            "- Analogies and metaphors\n"
            "- Emotional appeals\n"
            "- Hedging and qualifications\n"
            "- Repetition and emphasis\n\n"
            "What remains should be bare logical structure: factual "
            "premises, inferential steps connecting them, and the "
            "conclusions that follow.\n\n"
            "Submit your extraction by calling the submit_skeleton tool."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state

        if state.get("extraction_failed") and state.get("extraction_attempts", 0) > 0:
            return (
                "The previous extraction did not produce a usable "
                "result. Please try again.\n\n"
                "Call the submit_skeleton tool with EXACTLY these "
                "fields:\n"
                "- premises: a list of {id, text} objects, e.g. "
                '{"id": "P1", "text": "factual claim or assumption"}\n'
                "- inferences: a list of {id, from, text} objects, "
                'e.g. {"id": "I1", "from": ["P1", "P2"], "text": '
                '"what follows from those premises"}\n'
                "- conclusions: a list of {id, from, text} objects, "
                'e.g. {"id": "C1", "from": ["I1", "I2"], "text": '
                '"final conclusion"}\n'
                "- rich_summary: a short paragraph on the discussion's "
                "most persuasive rhetoric\n\n"
                "Every inference and conclusion MUST have a \"from\" "
                "field listing which premises or prior inferences it "
                "depends on. Use IDs like P1, P2, I1, I2, C1, C2."
            )

        return (
            "Review the discussion above and extract its logical "
            "skeleton.\n\n"
            "First, in the 'rich_summary' field, give a short "
            "paragraph capturing the original discussion's most "
            "persuasive arguments and rhetorical moves — the examples, "
            "analogies, and appeals that carried the most force.  This "
            "will later be contrasted with the bare logic.\n\n"
            "Then identify:\n"
            "1. **Premises** — factual claims, assumptions, or starting "
            "points that participants stated or relied on\n"
            "2. **Inferences** — logical steps where one or more premises "
            "(or prior inferences) are combined to reach a new claim\n"
            "3. **Conclusions** — the final claims that the argument "
            "arrives at\n\n"
            "Submit the skeleton by calling the submit_skeleton tool "
            "with your premises, inferences, and conclusions, plus "
            "the rich_summary field. Strip ALL rhetoric from the "
            "premises/inferences/conclusions — keep only the bare "
            "logical claims and their dependencies. Each inference "
            "and conclusion must reference which premises or prior "
            'inferences it depends on via the "from" field.'
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state

        # Capture the rich-reasoning summary if not already recorded.
        if not state.get("rich_reasoning_summary"):
            m = _RICH_SUMMARY_RE.search(content)
            if m:
                state["rich_reasoning_summary"] = m.group(1).strip()

        parsed = extract_json_block(content)

        if not parsed or not validate_skeleton(parsed):
            state["extraction_failed"] = True
            state["extraction_attempts"] = state.get("extraction_attempts", 0) + 1
            logger.warning(
                "Skeleton extraction attempt %d failed — %s",
                state["extraction_attempts"],
                "invalid structure" if parsed else "no JSON found",
            )
            return ProcessedResponse(display_content=content)

        state["skeleton"] = parsed
        state["skeleton_display"] = format_skeleton_display(parsed)
        state["extraction_failed"] = False

        logger.info(
            "Extracted skeleton: %d premises, %d inferences, %d conclusions",
            len(parsed["premises"]),
            len(parsed["inferences"]),
            len(parsed["conclusions"]),
        )
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_skeleton tool for this phase.

        Unlike blind_evaluate/evaluate_matrix, there is no state that
        makes the schema unsatisfiable (extraction always has a
        discussion to distill), so this always returns a spec.
        """
        return OutputToolSpec(
            name="submit_skeleton",
            description=(
                "Submit the extracted logical skeleton: premises, "
                "inferences, and conclusions (each inference/conclusion "
                "listing which prior ids it depends on via 'from'), "
                "plus a rich_summary paragraph capturing the original "
                "discussion's most persuasive rhetoric."
            ),
            parameters=SKELETON_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_skeleton payload via the shared function."""
        return validate_skeleton_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Store the submitted skeleton and render the same display.

        Mirrors ``process_response``'s successful branch: writes
        ``skeleton``, ``skeleton_display`` (via
        ``format_skeleton_display``), clears ``extraction_failed``, and
        captures ``rich_reasoning_summary`` from the payload's
        ``rich_summary`` field -- once only, exactly like the free-text
        ``_RICH_SUMMARY_RE`` capture in ``process_response``.
        """
        state = discussion.method_state
        skeleton = {key: payload[key]
                   for key in ("premises", "inferences", "conclusions")}
        skeleton_display = format_skeleton_display(skeleton)

        state["skeleton"] = skeleton
        state["skeleton_display"] = skeleton_display
        state["extraction_failed"] = False

        rich_summary = coerce_str(payload, "rich_summary")
        if not state.get("rich_reasoning_summary"):
            state["rich_reasoning_summary"] = rich_summary

        logger.info(
            "Extracted skeleton via submit_skeleton: %d premises, "
            "%d inferences, %d conclusions",
            len(skeleton["premises"]),
            len(skeleton["inferences"]),
            len(skeleton["conclusions"]),
        )

        display = (f"**Rich Summary:**\n\n{rich_summary}\n\n{skeleton_display}"
                  if rich_summary else skeleton_display)
        return ProcessedResponse(display_content=display)

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("skeleton"):
            return True
        if state.get("extraction_attempts", 0) >= MAX_EXTRACTION_ATTEMPTS:
            logger.warning(
                "Giving up on skeleton extraction after %d attempts",
                MAX_EXTRACTION_ATTEMPTS,
            )
            return True
        return False

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The moderator will now extract the pure logical skeleton "
            "of the discussion — stripping away all rhetoric, examples, "
            "and persuasive language to reveal the bare argument structure."
        )
