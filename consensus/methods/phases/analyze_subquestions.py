"""Analyze Sub-questions phase handler for Recursive Decomposition.

Each participant addresses every consolidated sub-question with
focused analysis.  Responses are parsed to extract per-sub-question
sections and accumulated in method_state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._decomposition_helpers import extract_subquestion_analyses

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AnalyzeSubquestionsHandler(PhaseHandler):
    """Phase 2: Focused analysis of each sub-question."""

    phase = Phase(
        name="analyze",
        display_name="Sub-Question Analysis",
        description=(
            "Each participant addresses every sub-question with "
            "substantive analysis, using structured headers."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        sq_list = "\n".join(
            f"{i + 1}. {sq}" for i, sq in enumerate(sub_questions)
        )
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "SUB-QUESTION ANALYSIS PHASE\n\n"
            "The group has identified the following sub-questions:\n"
            f"{sq_list}\n\n"
            "Address EACH sub-question with substantive analysis. "
            "Use this format:\n\n"
            "**Sub-question 1:** <your analysis>\n\n"
            "**Sub-question 2:** <your analysis>\n\n"
            "...\n\n"
            "For each sub-question, provide your best reasoning, "
            "evidence, and any caveats or uncertainties."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        n = len(discussion.method_state.get("sub_questions", []))
        return (
            f"It is your turn, {entity.name}. Address each of the "
            f"{n} sub-questions with substantive analysis. Use the "
            "**Sub-question N:** format for each."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has provided their analysis of all "
            "sub-questions. Briefly note key points and any notable "
            f"differences from prior analyses. Next: "
            f"{next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        analyses = state.setdefault("sub_question_analyses", {})

        extractions = extract_subquestion_analyses(content, len(sub_questions))
        for idx, analysis_text in extractions.items():
            key = str(idx)
            analyses.setdefault(key, []).append({
                "entity": entity.name,
                "analysis": analysis_text,
            })

        return ProcessedResponse(
            display_content=content,
            extracted_data={"analyses_extracted": len(extractions)},
        )
