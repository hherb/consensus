"""Skeleton Extraction phase handler for Recursive Self-Distillation.

The moderator strips the rich discussion down to a pure logical
skeleton: premises, inference steps, and conclusions — with all
rhetoric, examples, analogies, and emotional language removed.
Includes retry logic for failed JSON extraction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import extract_json_block
from ..phase_handler import PhaseHandler
from ._distillation_helpers import format_skeleton_display, validate_skeleton

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

MAX_EXTRACTION_ATTEMPTS = 3


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
            "conclusions that follow."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state

        if state.get("extraction_failed") and state.get("extraction_attempts", 0) > 0:
            return (
                "The previous extraction did not produce valid JSON. "
                "Please try again.\n\n"
                "You MUST output a JSON code block with EXACTLY this "
                "structure:\n"
                "```json\n"
                "{\n"
                '  "premises": [\n'
                '    {"id": "P1", "text": "factual claim or assumption"},\n'
                '    {"id": "P2", "text": "another premise"}\n'
                "  ],\n"
                '  "inferences": [\n'
                '    {"id": "I1", "from": ["P1", "P2"], '
                '"text": "what follows from those premises"},\n'
                '    {"id": "I2", "from": ["P1", "I1"], '
                '"text": "next inferential step"}\n'
                "  ],\n"
                '  "conclusions": [\n'
                '    {"id": "C1", "from": ["I1", "I2"], '
                '"text": "final conclusion"}\n'
                "  ]\n"
                "}\n"
                "```\n\n"
                "Every inference and conclusion MUST have a \"from\" "
                "field listing which premises or prior inferences it "
                "depends on. Use IDs like P1, P2, I1, I2, C1, C2."
            )

        return (
            "Review the discussion above and extract its logical "
            "skeleton.\n\n"
            "Identify:\n"
            "1. **Premises** — factual claims, assumptions, or starting "
            "points that participants stated or relied on\n"
            "2. **Inferences** — logical steps where one or more premises "
            "(or prior inferences) are combined to reach a new claim\n"
            "3. **Conclusions** — the final claims that the argument "
            "arrives at\n\n"
            "Output the skeleton as a JSON code block:\n"
            "```json\n"
            "{\n"
            '  "premises": [\n'
            '    {"id": "P1", "text": "..."},\n'
            '    {"id": "P2", "text": "..."}\n'
            "  ],\n"
            '  "inferences": [\n'
            '    {"id": "I1", "from": ["P1", "P2"], "text": "..."},\n'
            '    {"id": "I2", "from": ["I1"], "text": "..."}\n'
            "  ],\n"
            '  "conclusions": [\n'
            '    {"id": "C1", "from": ["I1", "I2"], "text": "..."}\n'
            "  ]\n"
            "}\n"
            "```\n\n"
            "Strip ALL rhetoric — keep only the bare logical claims "
            "and their dependencies. Each inference must reference "
            "which premises or prior inferences it depends on via "
            'the "from" field.'
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
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
        return ProcessedResponse(
            display_content=content,
            extracted_data={"skeleton": parsed},
        )

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
