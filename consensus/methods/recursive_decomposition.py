"""Recursive Decomposition — LLM-native decompose-and-recompose method.

Participants collaboratively break a complex question into sub-questions,
each sub-question is analyzed by all participants, cross-cutting patterns
are identified, and results are recomposed into a coherent answer.

Phases:
  1. DECOMPOSE   — Participants propose sub-questions; moderator consolidates
  2. ANALYZE     — Each participant analyses every sub-question
  3. INTEGRATE   — Identify reinforcements, conflicts, gaps across analyses
  4. RECOMPOSE   — Synthesize a unified answer to the original question
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.decompose import DecomposeHandler
from .phases.analyze_subquestions import AnalyzeSubquestionsHandler
from .phases.integrate_subquestions import IntegrateSubquestionsHandler
from .phases.recompose import RecomposeHandler

if TYPE_CHECKING:
    from ..models import Discussion


class RecursiveDecomposition(DiscussionMethod):
    """Recursive Decomposition — decompose, analyse, integrate, recompose."""

    name = "recursive_decomposition"
    display_name = "Recursive Decomposition"
    description = (
        "An LLM-native method: collaboratively decompose a complex "
        "question into sub-questions, analyse each through multi-"
        "participant discussion, identify cross-cutting patterns, and "
        "recompose a unified answer.  Exploits structured decomposition "
        "and synthesis across abstraction levels."
    )
    phase_handlers = (
        DecomposeHandler(),
        AnalyzeSubquestionsHandler(),
        IntegrateSubquestionsHandler(),
        RecomposeHandler(),
    )

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        sq_list = "\n".join(
            f"{i + 1}. {sq}" for i, sq in enumerate(sub_questions)
        )
        return (
            "The Recursive Decomposition analysis is complete.\n\n"
            f"Original question: \"{discussion.topic}\"\n\n"
            "The group decomposed this into the following sub-questions:\n"
            f"{sq_list}\n\n"
            "Provide a comprehensive final synthesis:\n"
            "1. **Sub-question findings** — Summarize the key findings "
            "for each sub-question, noting where participants agreed "
            "and diverged\n"
            "2. **Cross-cutting patterns** — What reinforcements, "
            "conflicts, and emergent insights were identified during "
            "integration?\n"
            "3. **Consolidated answer** — Provide a clear, unified "
            "answer to the original question that accounts for all "
            "sub-analyses\n"
            "4. **Confidence and caveats** — What aspects of the answer "
            "are well-supported vs. uncertain?\n"
            "5. **Decomposition assessment** — Were any sub-questions "
            "too complex for single-level analysis and would benefit "
            "from further decomposition?\n\n"
            "Ground your synthesis in the specific analyses and "
            "integrations provided by participants."
        )
