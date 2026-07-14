"""Deep-dive expansion phase handler for Tree of Thoughts (issue #26).

Each participant deepens the surviving beam via the forced
``submit_expansions`` output tool (issue #23): per surviving thought, a
refinement (how to strengthen and concretise it) and the obstacles that
could make it fail.  A fenced/inline JSON parse remains the
human/fallback path.  Expansions are depth-tagged and shown in the next
scoring pass so re-scores are informed by the deep-dive; the thought
texts themselves stay immutable — label stability is what makes
re-scoring and convergence meaningful.

``next_phase`` always jumps back to ``score`` — this phase is only
entered when the prune phase chose to continue the loop, and the linear
successor (``synthesise``) is reachable only via prune's jump.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import extract_json_payload
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._tot_analysis import format_thoughts
from ._tot_helpers import (
    EXPANSIONS_TOOL_PARAMETERS,
    current_depth,
    eligible_thoughts,
    record_expansions,
    thought_label,
    validate_expansions_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class ExpandThoughtsHandler(PhaseHandler):
    """Phase 4: Deep-dive the surviving beam (refine + obstacles)."""

    phase = Phase(
        name="expand",
        display_name="Deep-Dive Expansion",
        description=(
            "Each participant deepens the surviving approaches: how to "
            "strengthen and concretise each one, and what obstacles "
            "could make it fail.  The survivors are then re-scored."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"expansions": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        survivors = eligible_thoughts(state)
        base = (
            f"You are {entity.name}, participating in a Tree of Thoughts "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "DEEP-DIVE EXPANSION PHASE\n\n"
        )
        if not survivors:
            return base + (
                "No approaches survived the prune.  Give a brief "
                "reflection on the topic instead."
            )
        return base + (
            "These approaches survived the prune:\n"
            f"{format_thoughts(survivors)}\n\n"
            "Deep-dive each survivor you can add value on: refine it "
            "(next steps, scope, mechanism — make it concrete) and name "
            "the obstacles that could make it fail.  Build on others' "
            "points where useful; this phase is collaborative.\n\n"
            "Submit your deep-dives by calling the submit_expansions "
            "tool with one entry per approach (its numeric thought_id "
            "from the labels above, your refinement, and the obstacles), "
            "plus your overall reasoning."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        survivors = eligible_thoughts(discussion.method_state)
        if not survivors:
            return (
                f"{entity.name}, no approaches survived — give a brief "
                "reflection instead."
            )
        labels = ", ".join(thought_label(t["id"]) for t in survivors)
        return (
            f"{entity.name}, deep-dive the surviving approaches "
            f"({labels}) by calling the submit_expansions tool with your "
            "refinements and obstacles."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has deep-dived the survivors.  Briefly "
            "highlight any new obstacle or refinement that changes the "
            f"picture, then invite {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Context filtering — anonymise authorship (whole-method blindness)
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        """Deep-dives build on approaches, not on their authors."""
        return anonymise_content(content, discussion)

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        items = extract_json_payload(content, "expansions")
        accepted = (record_expansions(state, entity, items,
                                      current_depth(state))
                    if isinstance(items, list) else 0)
        if not accepted:
            logger.warning(
                "Could not extract expansions from %s's response",
                entity.name)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_expansions tool for this phase.

        Returns ``None`` when no thoughts survive (nothing could pass
        validation, so forcing the tool would burn every retry — the
        ``score_options.py`` degenerate-guard pattern).
        """
        survivors = eligible_thoughts(discussion.method_state)
        if not survivors:
            return None
        return OutputToolSpec(
            name="submit_expansions",
            description=(
                "Submit your deep-dive of the surviving approaches: one "
                "entry per approach with its refinement and obstacles, "
                "plus your overall reasoning.\n"
                f"Survivors:\n{format_thoughts(survivors)}"
            ),
            parameters=EXPANSIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        beam_ids = {t["id"]
                    for t in eligible_thoughts(discussion.method_state)}
        return validate_expansions_payload(payload, beam_ids)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_expansions(state, entity, payload["expansions"],
                          current_depth(state))
        reasoning = str(payload.get("reasoning") or "").strip()
        lines = []
        for entry in payload["expansions"]:
            line = (f"{thought_label(int(entry['thought_id']))}: "
                    f"{str(entry.get('refinement') or '').strip()}")
            obstacles = entry.get("obstacles")
            if isinstance(obstacles, list) and obstacles:
                line += ("\n  Obstacles: "
                         + "; ".join(str(o) for o in obstacles))
            lines.append(line)
        rendered = "\n".join(lines)
        display = f"{reasoning}\n\n{rendered}" if reasoning else rendered
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement & routing — always loop back to scoring
    # ------------------------------------------------------------------

    def next_phase(self, discussion: Discussion) -> str | None:
        """Loop back to re-score the survivors.

        This phase only runs when prune chose to continue, so the loop
        edge is unconditional; prune's own routing is the loop's exit.
        """
        return "score"

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        survivors = eligible_thoughts(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The surviving approaches will now be deep-dived — refined, "
            "concretised, and stress-checked for obstacles — before "
            "being re-scored:\n"
            f"{format_thoughts(survivors)}"
        )
