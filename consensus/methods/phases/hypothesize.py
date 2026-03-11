"""Hypothesize phase handler for Analysis of Competing Hypotheses.

Participants propose competing hypotheses.  Hypotheses are extracted
from responses using numbered-list parsing and deduplicated by word
overlap similarity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

# Minimum character length for a hypothesis to be considered meaningful
MIN_HYPOTHESIS_LENGTH = 10
# Word overlap ratio above which two hypotheses are considered duplicates
SIMILARITY_THRESHOLD = 0.7


class HypothesizeHandler(PhaseHandler):
    """Phase 1: Generate competing hypotheses."""

    phase = Phase(
        name="hypothesize",
        display_name="Hypothesis Generation",
        description=(
            "Each participant proposes 2-3 competing hypotheses that "
            "could explain or answer the question.  Be creative — "
            "include hypotheses you disagree with."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "hypotheses": [],
            "evidence": [],
            "matrix": {},
            "next_evidence_id": 1,
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        base = (
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        return base + (
            "HYPOTHESIS GENERATION PHASE\n\n"
            "Propose 2-3 competing hypotheses that could explain or "
            "answer the question.  IMPORTANT: include hypotheses you "
            "disagree with — ACH requires evaluating ALL plausible "
            "explanations.\n\n"
            "Format each hypothesis on its own line:\n"
            "1. <hypothesis text>\n"
            "2. <hypothesis text>\n"
            "3. <hypothesis text>\n\n"
            "For each, provide 1-2 sentences of context about why "
            "it is plausible."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Propose 2-3 competing "
            "hypotheses.  Include at least one you personally doubt."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their hypotheses.  "
            "Briefly note the hypotheses and how they complement or "
            "overlap with previously proposed ones.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_hyps = parse_numbered_list(content, min_length=MIN_HYPOTHESIS_LENGTH)

        if new_hyps:
            existing = state.get("hypotheses", [])
            for h in new_hyps:
                if not any(word_overlap_similar(h, e, threshold=SIMILARITY_THRESHOLD)
                           for e in existing):
                    existing.append(h)
            state["hypotheses"] = existing

        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_hypotheses": new_hyps},
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("hypotheses"))
                and state.get("phase_round", 1) > 1)

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )
