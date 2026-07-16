"""Surface Assumptions phase handler for Key Assumptions Check.

Participants identify the key assumptions underlying the discussion
topic via the forced ``submit_assumptions`` output tool (issue #23);
free-text numbered-list parsing remains as the fallback path for
humans and non-tool turns.  Assumptions are deduplicated by word
overlap similarity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str, parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

# Minimum character length for an assumption to be considered meaningful
MIN_ASSUMPTION_LENGTH = 10
# Word overlap ratio above which two assumptions are considered duplicates
SIMILARITY_THRESHOLD = 0.7
# Give up and advance after this many rounds even without parsed
# assumptions — an unparseable group must not loop forever (issue #15).
MAX_SURFACE_ROUNDS = 3

#: JSON Schema for the submit_assumptions output tool (issue #23).
ASSUMPTIONS_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("Key assumptions underlying this topic, "
                            "question, or any proposed answer — factual, "
                            "causal, logical, value-based, or scope "
                            "assumptions.  Include assumptions that seem "
                            "obvious; those are often the most dangerous "
                            "when wrong."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale for these assumptions: why "
                            "each is being taken for granted and what "
                            "kind of assumption it is."),
        },
    },
    "required": ["assumptions", "reasoning"],
}


def validate_assumptions_payload(payload: dict) -> str:
    """Return '' if a submit_assumptions payload is usable, else an error.

    Applies the same substantive-length bar as the free-text path
    (``parse_numbered_list`` with ``min_length=MIN_ASSUMPTION_LENGTH``,
    which keeps items of length ``>= MIN_ASSUMPTION_LENGTH``).
    """
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions:
        return "'assumptions' must be a non-empty array of assumption strings."
    for a in assumptions:
        if not isinstance(a, str) or len(a.strip()) < MIN_ASSUMPTION_LENGTH:
            return (
                "Each assumption must be a substantive string of at "
                f"least {MIN_ASSUMPTION_LENGTH} characters describing a "
                f"specific assumption (got: {a!r})."
            )
    if not coerce_str(payload, "reasoning"):
        return "'reasoning' must contain your rationale for these assumptions."
    return ""


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
            "Aim for 3-5 assumptions.  Include assumptions that seem "
            "obvious — those are often the most dangerous when wrong.\n\n"
            "Submit your assumptions by calling the submit_assumptions "
            "tool with an array of assumption strings — each a "
            "complete, specific statement — plus your rationale in the "
            "'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Identify 3-5 key "
            "assumptions underlying this topic.  Include both obvious "
            "and hidden assumptions.  Submit them by calling the "
            "submit_assumptions tool."
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
        new_assumptions = parse_numbered_list(content,
                                              min_length=MIN_ASSUMPTION_LENGTH)
        if new_assumptions:
            existing = state.get("assumptions", [])
            for a in new_assumptions:
                if not any(word_overlap_similar(a, e, threshold=SIMILARITY_THRESHOLD)
                           for e in existing):
                    existing.append(a)
            state["assumptions"] = existing
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        """Declare the forced submit_assumptions tool for this phase."""
        return OutputToolSpec(
            name="submit_assumptions",
            description=("Submit key assumptions as an array of "
                         "assumption strings, plus your reasoning."),
            parameters=ASSUMPTIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_assumptions payload via the shared function."""
        return validate_assumptions_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Dedup submitted assumptions against existing ones and append.

        Mirrors ``process_response``'s exact dedup rule: a submitted
        assumption is dropped if it is word-overlap similar (threshold
        ``SIMILARITY_THRESHOLD``) to any assumption already in
        ``state["assumptions"]``.  Assumptions accumulate here across
        participants and rounds, so accepted items from earlier turns
        are never replaced.  The display renders the reasoning first,
        followed by a numbered list of only the assumptions accepted
        this turn (i.e. excluding any submitted duplicates).
        """
        state = discussion.method_state
        submitted = [str(a).strip() for a in payload["assumptions"]
                     if str(a).strip()]
        existing = state.get("assumptions", [])
        accepted = []
        for a in submitted:
            if not any(word_overlap_similar(a, e, threshold=SIMILARITY_THRESHOLD)
                       for e in existing):
                existing.append(a)
                accepted.append(a)
        state["assumptions"] = existing

        reasoning = coerce_str(payload, "reasoning")
        numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_SURFACE_ROUNDS:
            logger.warning(
                "Surface Assumptions phase reached round %d; advancing "
                "with %d assumption(s) collected.",
                phase_round, len(state.get("assumptions", [])),
            )
            return True
        return bool(state.get("assumptions")) and phase_round > 1
