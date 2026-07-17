"""Parallel approach generation phase handler for Tree of Thoughts.

Each participant independently proposes distinct solution approaches
("thoughts") via the forced ``submit_thoughts`` output tool (issue
#23); free-text numbered-list parsing remains the human/fallback path.
Context is anonymised (Delphi-style) per issue #26 — approaches are
judged on content, not authorship, to avoid anchoring.  If no thoughts
at all are collected after ``MAX_PROPOSE_ROUNDS``, the method aborts
early — every later phase needs a candidate list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._generation_giveup import GenerationGiveUpMixin
from ._tot_helpers import (
    MAX_PROPOSE_ROUNDS,
    MIN_THOUGHT_LENGTH,
    THOUGHTS_TOOL_PARAMETERS,
    record_thoughts,
    thought_label,
    validate_thoughts_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ProposeThoughtsHandler(GenerationGiveUpMixin, PhaseHandler):
    """Phase 1: Anonymised independent generation of solution approaches."""

    phase = Phase(
        name="propose",
        display_name="Parallel Approach Generation",
        description=(
            "Each participant independently proposes distinct solution "
            "approaches.  Contributions are anonymised — approaches are "
            "judged on content, not authorship."
        ),
        rounds=1,
    )

    # Give-up containment (shared GenerationGiveUpMixin shape).
    giveup_state_key = "thoughts"
    giveup_max_rounds = MAX_PROPOSE_ROUNDS
    giveup_generation_label = "Approach generation"
    giveup_collected_noun = "thought"
    giveup_method_short = "Tree of Thoughts"
    giveup_method_title = "Tree of Thoughts"
    giveup_usable_noun = "approaches"
    giveup_skipped_phases = "the scoring, pruning, and expansion phases"

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"thoughts": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Tree of Thoughts "
            "session — the group explores parallel solution paths, "
            "scores them, prunes to the strongest few, and deepens "
            "those iteratively.\n"
            f"Topic: {discussion.topic}\n\n"
            "APPROACH GENERATION PHASE\n\n"
            "Independently propose 2-5 genuinely distinct solution "
            "approaches — different strategies, not variations of one "
            "idea.  IMPORTANT: Do not react to or build on others' "
            "contributions; this is your independent thinking.  Breadth "
            "beats polish: include an unconventional approach.\n\n"
            "Submit your approaches by calling the submit_thoughts tool "
            "with an array of approach strings — each a complete, "
            "specific, self-contained strategy — plus a brief rationale "
            "in the 'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Independently propose "
            "2-5 genuinely distinct approaches by calling the "
            "submit_thoughts tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            "A set of candidate approaches has been received.  Do NOT "
            "reveal, quote, or evaluate any of them — independent "
            "generation requires that participants do not anchor on "
            f"each other.  Simply invite the next participant.\n\n"
            f"{next_speaker_name}, please independently propose your "
            "candidate approaches on the topic."
        )

    # ------------------------------------------------------------------
    # Context filtering — anonymise authorship
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = parse_numbered_list(content, min_length=MIN_THOUGHT_LENGTH)
        if items:
            record_thoughts(state, entity, items)
        else:
            logger.warning(
                "Could not extract thoughts from %s's response",
                entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_thoughts",
            description=("Submit your independent candidate solution "
                         "approaches as an array of complete strategy "
                         "strings, plus your reasoning."),
            parameters=THOUGHTS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_thoughts_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        texts = [str(t).strip() for t in payload["thoughts"]
                 if str(t).strip()]
        accepted = record_thoughts(state, entity, texts)
        reasoning = str(payload.get("reasoning") or "").strip()
        numbered = "\n".join(
            f"{n}. {t['text']} ({thought_label(t['id'])})"
            for n, t in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # Phase advancement, give-up, and early-method-end messaging are
    # inherited from GenerationGiveUpMixin (parametrised above).
