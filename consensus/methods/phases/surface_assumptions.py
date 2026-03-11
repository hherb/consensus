"""Surface Assumptions phase handler for Key Assumptions Check.

Participants identify the key assumptions underlying the discussion
topic.  Assumptions are extracted from responses and deduplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SurfaceAssumptionsHandler(PhaseHandler):
    """Phase 1: Surface hidden assumptions."""

    phase = Phase(
        name="surface",
        display_name="Surface Assumptions",
        description=(
            "Identify the key assumptions underlying the question, "
            "the prevailing view, or any proposed answer.  These may "
            "be factual, causal, logical, or value-based assumptions."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"assumptions": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        base = (
            f"You are {entity.name}, participating in a Key Assumptions Check.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        return base + (
            "ASSUMPTION SURFACING PHASE\n\n"
            "Identify the key assumptions that underlie this topic, "
            "question, or any proposed answer.  Consider:\n\n"
            "- **Factual assumptions** — What facts are taken for granted?\n"
            "- **Causal assumptions** — What cause-effect relationships "
            "are assumed?\n"
            "- **Logical assumptions** — What logical connections are "
            "assumed to hold?\n"
            "- **Value assumptions** — What values or priorities are "
            "implicitly assumed?\n"
            "- **Scope assumptions** — What boundaries or constraints "
            "are assumed?\n\n"
            "Format each assumption as a numbered item:\n"
            "1. <assumption>\n"
            "2. <assumption>\n"
            "...\n\n"
            "Aim for 3-5 assumptions.  Include assumptions that seem "
            "obvious — those are often the most dangerous when wrong."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Identify 3-5 key "
            "assumptions underlying this topic.  Include both obvious "
            "and hidden assumptions."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has identified their key assumptions.  "
            "Briefly note which assumptions are new vs. overlapping "
            f"with previously surfaced ones.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_assumptions = parse_numbered_list(content)
        if new_assumptions:
            existing = state.get("assumptions", [])
            for a in new_assumptions:
                if not any(word_overlap_similar(a, e) for e in existing):
                    existing.append(a)
            state["assumptions"] = existing
        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_assumptions": new_assumptions},
        )

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("assumptions"))
                and state.get("phase_round", 1) > 1)
