"""Decompose phase handler for Recursive Decomposition.

Participants propose 3-7 sub-questions that collectively address the
main question.  Sub-questions are extracted via numbered-list parsing
and deduplicated by word overlap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

MIN_SUBQUESTION_LENGTH = 10
SIMILARITY_THRESHOLD = 0.7


class DecomposeHandler(PhaseHandler):
    """Phase 1: Collaborative question decomposition."""

    phase = Phase(
        name="decompose",
        display_name="Decomposition",
        description=(
            "Each participant proposes 3-7 independent sub-questions that, "
            "if answered thoroughly, would collectively address the main "
            "question."
        ),
        rounds=1,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "sub_questions": [],
            "sub_question_analyses": {},
        }

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "DECOMPOSITION PHASE\n\n"
            "Break the main question into 3-7 independent sub-questions "
            "that, if each were answered thoroughly, would collectively "
            "provide a comprehensive answer to the main question.\n\n"
            "Guidelines:\n"
            "- Each sub-question should be self-contained and answerable "
            "independently\n"
            "- Cover different dimensions or aspects of the problem\n"
            "- Avoid sub-questions that simply restate the main question "
            "in different words\n"
            "- Prefer specific, concrete sub-questions over vague ones\n\n"
            "Format each sub-question on its own line:\n"
            "1. <sub-question>\n"
            "2. <sub-question>\n"
            "...\n\n"
            "For each, provide 1-2 sentences explaining why this "
            "sub-question matters for answering the main question."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Propose 3-7 sub-questions "
            "that, if each were answered thoroughly, would collectively "
            "address the main question."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their sub-questions. Briefly "
            "note the sub-questions proposed and how they complement or "
            f"overlap with previously proposed ones. Next: "
            f"{next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_sqs = parse_numbered_list(content, min_length=MIN_SUBQUESTION_LENGTH)

        if new_sqs:
            existing = state.get("sub_questions", [])
            for sq in new_sqs:
                if not any(word_overlap_similar(sq, e,
                           threshold=SIMILARITY_THRESHOLD)
                           for e in existing):
                    existing.append(sq)
            state["sub_questions"] = existing

        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_sub_questions": new_sqs},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("sub_questions"))
                and state.get("phase_round", 1) > 1)
