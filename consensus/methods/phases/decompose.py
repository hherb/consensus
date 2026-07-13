"""Decompose phase handler for Recursive Decomposition.

Participants propose 3-7 sub-questions that collectively address the
main question.  Sub-questions are extracted via numbered-list parsing
and deduplicated by word overlap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

# Minimum character length for a sub-question to be considered meaningful
MIN_SUBQUESTION_LENGTH = 10
# Word overlap ratio above which two sub-questions are considered duplicates
SIMILARITY_THRESHOLD = 0.7
# Give up and advance after this many rounds even without parsed
# sub-questions — an unparseable group must not loop forever (issue #15).
MAX_DECOMPOSE_ROUNDS = 3

#: JSON Schema for the submit_subquestions output tool (issue #23).
SUBQUESTIONS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("Independent sub-questions that, if each were "
                            "answered thoroughly, would collectively "
                            "provide a comprehensive answer to the main "
                            "question.  Each should be self-contained, "
                            "cover a distinct dimension of the problem, "
                            "and avoid simply restating the main question."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale for these sub-questions: why "
                            "each matters for answering the main "
                            "question."),
        },
    },
    "required": ["sub_questions", "reasoning"],
}


def validate_subquestions_payload(payload: dict) -> str:
    """Return '' if a submit_subquestions payload is usable, else an error.

    Applies the same substantive-length bar as the free-text path
    (``parse_numbered_list`` with ``min_length=MIN_SUBQUESTION_LENGTH``,
    which keeps items of length ``>= MIN_SUBQUESTION_LENGTH``).
    """
    sub_questions = payload.get("sub_questions")
    if not isinstance(sub_questions, list) or not sub_questions:
        return ("'sub_questions' must be a non-empty array of "
                "sub-question strings.")
    for sq in sub_questions:
        if not isinstance(sq, str) or len(sq.strip()) < MIN_SUBQUESTION_LENGTH:
            return (
                "Each sub-question must be a substantive string of at "
                f"least {MIN_SUBQUESTION_LENGTH} characters describing a "
                f"specific sub-question (got: {sq!r})."
            )
    if not str(payload.get("reasoning", "")).strip():
        return "'reasoning' must contain your rationale for these sub-questions."
    return ""


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

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "sub_questions": [],
            "sub_question_analyses": {},
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

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
            "Submit your sub-questions by calling the submit_subquestions "
            "tool with an array of sub-question strings — each a "
            "complete, self-contained question — plus your rationale in "
            "the 'reasoning' field explaining why each sub-question "
            "matters for answering the main question."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Propose 3-7 sub-questions "
            "that, if each were answered thoroughly, would collectively "
            "address the main question. Submit them by calling the "
            "submit_subquestions tool."
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

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

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

        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        """Declare the forced submit_subquestions tool for this phase."""
        return OutputToolSpec(
            name="submit_subquestions",
            description=("Submit sub-questions as an array of "
                         "sub-question strings, plus your reasoning."),
            parameters=SUBQUESTIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_subquestions payload via the shared function."""
        return validate_subquestions_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Dedup submitted sub-questions against existing ones and append.

        Mirrors ``process_response``'s exact dedup rule: a submitted
        sub-question is dropped if it is word-overlap similar (threshold
        ``SIMILARITY_THRESHOLD``) to any sub-question already in
        ``state["sub_questions"]``.  Sub-questions accumulate here
        across participants and rounds, so accepted items from earlier
        turns are never replaced.  The display renders the reasoning
        first, followed by a numbered list of only the sub-questions
        accepted this turn (i.e. excluding any submitted duplicates).
        """
        state = discussion.method_state
        submitted = [str(sq).strip() for sq in payload["sub_questions"]
                     if str(sq).strip()]
        existing = state.get("sub_questions", [])
        accepted = []
        for sq in submitted:
            if not any(word_overlap_similar(sq, e,
                       threshold=SIMILARITY_THRESHOLD)
                       for e in existing):
                existing.append(sq)
                accepted.append(sq)
        state["sub_questions"] = existing

        reasoning = str(payload.get("reasoning", "")).strip()
        numbered = "\n".join(f"{i}. {sq}" for i, sq in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_DECOMPOSE_ROUNDS:
            return True
        return bool(state.get("sub_questions")) and phase_round > 1
