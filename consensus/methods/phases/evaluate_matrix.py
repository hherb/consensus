"""Matrix evaluation phase handler for Analysis of Competing Hypotheses.

Each participant rates every hypothesis against every piece of evidence
using +/-/0 ratings, submitted via the forced ``submit_matrix_ratings``
output tool (issue #23); free-text JSON parsing remains as the fallback
path for humans and non-tool turns.  Ratings are stored per entity in
the discussion's method state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str, extract_json_payload
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

#: Accepted rating symbols.  Must match exactly what the downstream
#: majority-vote aggregation recognizes -- ``ach.py``'s
#: ``_aggregate_matrix`` only counts "+", "-", and "0" votes (any other
#: string is silently treated as unrecognized), so the structured-output
#: enum below is bound to the same set: no more, no less.
RATING_SYMBOLS: tuple[str, ...] = ("+", "-", "0")

#: JSON Schema for the submit_matrix_ratings output tool (issue #23).
#: ``ratings`` nests two levels of dynamic keys -- hypothesis label
#: (H1, H2, ...) then evidence label (E<id>, ...) -- so both levels use
#: ``additionalProperties`` (the pattern ``BELIEFS_TOOL_PARAMETERS`` in
#: ``_belief_helpers.py`` uses for its single level of dynamic keys).
MATRIX_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "object",
            "description": (
                "Map of every hypothesis label (H1, H2, ...) to an "
                "object mapping every evidence label (E1, E2, ...) to "
                "your rating for that hypothesis/evidence pair."
            ),
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {
                    "type": "string",
                    "enum": list(RATING_SYMBOLS),
                },
            },
        },
        "reasoning": {
            "type": "string",
            "description": (
                "Your rationale for these ratings -- explain your most "
                "important inconsistency ratings: why does that "
                "evidence contradict that hypothesis?"
            ),
        },
    },
    "required": ["ratings", "reasoning"],
}


def validate_matrix_payload(payload: dict, hypotheses: list[str],
                            evidence: list[dict]) -> str:
    """Return '' if a submit_matrix_ratings payload is usable, else an error.

    Hypothesis keys must be a subset of the framed H-labels and evidence
    keys a subset of the recorded E-labels (unknown labels are named in
    the error, along with the valid set).  Every rating value must be
    one of ``RATING_SYMBOLS``.  Coverage may be partial: the free-text
    ``_parse_ratings`` path stores whatever ratings JSON a participant
    supplies with no completeness check (a partial matrix just displays
    "?" for missing cells and defaults to neutral in aggregation), so
    this validator holds the same bar rather than a stricter one.
    """
    ratings = payload.get("ratings")
    if not isinstance(ratings, dict) or not ratings:
        return ("'ratings' must be a non-empty object mapping each "
                "hypothesis label (H1, H2, ...) to a ratings object.")

    valid_h = [f"H{i}" for i in range(1, len(hypotheses) + 1)]
    unknown_h = [key for key in ratings if key not in valid_h]
    if unknown_h:
        return (f"Unknown hypothesis label(s) {unknown_h}. "
                f"Valid labels: {valid_h}.")

    valid_e = [f"E{e['id']}" for e in evidence]
    for hkey, row in ratings.items():
        if not isinstance(row, dict) or not row:
            return (f"The ratings for '{hkey}' must be a non-empty object "
                    "mapping each evidence label to a rating.")
        unknown_e = [key for key in row if key not in valid_e]
        if unknown_e:
            return (f"Unknown evidence label(s) {unknown_e} for '{hkey}'. "
                    f"Valid labels: {valid_e}.")
        for ekey, rating in row.items():
            if rating not in RATING_SYMBOLS:
                return (f"The rating for '{hkey}'/'{ekey}' must be one of "
                        f"{list(RATING_SYMBOLS)} (got: {rating!r}).")

    if not coerce_str(payload, "reasoning"):
        return "'reasoning' must contain your rationale for these ratings."

    return ""


class EvaluateMatrixHandler(PhaseHandler):
    """Phase 3: Rate hypotheses against evidence."""

    phase = Phase(
        name="evaluate",
        display_name="Matrix Evaluation",
        description=(
            "Rate each hypothesis against each piece of evidence.  "
            "Focus especially on INCONSISTENCIES — evidence that "
            "contradicts a hypothesis is more diagnostic than evidence "
            "that supports it."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])

        base = (
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        # Degenerate matrix (no hypotheses or no evidence):
        # get_output_tool returns None, so no submit_matrix_ratings
        # tool is offered — don't instruct the model to call it.
        if not hypotheses or not evidence:
            return base + (
                "MATRIX EVALUATION PHASE\n\n"
                "No rating matrix could be formed (missing hypotheses "
                "or evidence). Give a brief qualitative assessment of "
                "how the available material bears on the topic instead."
            )

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        ev_list = "\n".join(
            f"  E{e['id']}: {e['text']} (Source: {e.get('source', '?')})"
            for e in evidence
        )
        return base + (
            "MATRIX EVALUATION PHASE\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Evidence:\n{ev_list}\n\n"
            "Rate EACH hypothesis against EACH piece of evidence.\n\n"
            "Use these ratings:\n"
            "  + (consistent) — the evidence is what you'd expect if "
            "this hypothesis were true\n"
            "  - (inconsistent) — the evidence contradicts or is unlikely "
            "under this hypothesis\n"
            "  0 (neutral) — the evidence doesn't meaningfully "
            "differentiate\n\n"
            "Submit your ratings by calling the submit_matrix_ratings "
            "tool, mapping each hypothesis label (H1, H2, ...) to an "
            "object of evidence labels (E1, E2, ...) to your rating, "
            "plus your reasoning in the 'reasoning' field — explain "
            "your most important inconsistency ratings: why does that "
            "evidence contradict that hypothesis?"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        if not state.get("hypotheses") or not state.get("evidence"):
            return (
                f"{entity.name}, no rating matrix could be formed — "
                "give a brief qualitative assessment instead."
            )
        return (
            f"{entity.name}, evaluate each hypothesis against each piece "
            "of evidence using the +/-/0 rating system by calling the "
            "submit_matrix_ratings tool with your ratings and reasoning."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has submitted their evaluation matrix.  "
            "Note any ratings that differ significantly from previous "
            f"evaluators.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        ratings = self._parse_ratings(content)

        if ratings:
            state.setdefault("matrix", {})[str(entity.id)] = ratings

            # Augment display with formatted matrix
            matrix_text = self._format_rating_matrix(ratings, discussion)
            display = f"{content}\n\n---\n{matrix_text}"
        else:
            logger.warning(
                "Could not extract ratings from %s's evaluation",
                entity.name,
            )
            display = content

        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        """Declare the forced submit_matrix_ratings tool for this phase.

        Returns ``None`` when there is nothing to rate (no hypotheses
        or no evidence): no submit_matrix_ratings payload could pass
        validation, so forcing the tool would burn every retry.  Falls
        through to the free-text path instead (pattern shared with
        prior_beliefs.py / blind_evaluate.py).
        """
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])
        if not hypotheses or not evidence:
            return None

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        ev_list = "\n".join(f"  E{e['id']}: {e['text']}" for e in evidence)
        return OutputToolSpec(
            name="submit_matrix_ratings",
            description=(
                "Submit your ratings for each hypothesis against each "
                'piece of evidence, using "+" (consistent), "-" '
                '(inconsistent), or "0" (neutral), plus your reasoning.\n'
                f"Hypotheses:\n{hyp_list}\n\nEvidence:\n{ev_list}"
            ),
            parameters=MATRIX_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_matrix_ratings payload via the shared function."""
        state = discussion.method_state
        return validate_matrix_payload(payload, state.get("hypotheses", []),
                                       state.get("evidence", []))

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Store the submitted ratings and render the same matrix display.

        Writes ``state["matrix"][str(entity.id)]`` in exactly the shape
        ``_parse_ratings`` produces (hypothesis label -> evidence label
        -> rating string), so ``ach.py``'s aggregation and
        ``_format_rating_matrix`` work regardless of which path a turn
        took.
        """
        state = discussion.method_state
        ratings = {hkey: dict(row)
                  for hkey, row in payload["ratings"].items()}
        state.setdefault("matrix", {})[str(entity.id)] = ratings

        matrix_text = self._format_rating_matrix(ratings, discussion)
        reasoning = coerce_str(payload, "reasoning")
        display = (f"{reasoning}\n\n---\n{matrix_text}" if reasoning
                  else matrix_text)
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        return discussion.method_state.get("phase_round", 1) > 1

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        evidence = state.get("evidence", [])
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"{len(evidence)} pieces of evidence have been gathered.  "
            "Each participant will now rate every hypothesis against "
            "every piece of evidence using +/-/0 ratings."
        )

    # ------------------------------------------------------------------
    # Parsing helpers (ACH-specific)
    # ------------------------------------------------------------------

    def _parse_ratings(self, content: str) -> dict[str, dict[str, str]]:
        """Extract the rating matrix JSON from content.

        Delegates to the shared fenced + inline balanced-brace scanner
        (``parsing.extract_json_payload``); differs only in its return
        contract — always a dict, ``{}`` when nothing usable was found
        (a non-mapping ``ratings`` value included).
        """
        data = extract_json_payload(content, "ratings")
        return data if isinstance(data, dict) else {}

    def _format_rating_matrix(self, ratings: dict,
                              discussion: Discussion) -> str:
        """Format a single evaluator's rating matrix as markdown."""
        hypotheses = discussion.method_state.get("hypotheses", [])
        evidence = discussion.method_state.get("evidence", [])

        if not ratings or not evidence:
            return ""

        lines = ["**Rating Matrix:**", ""]

        # Header
        e_headers = " | ".join(f"E{e['id']}" for e in evidence)
        lines.append(f"| | {e_headers} |")
        lines.append(f"|---|{'---|' * len(evidence)}")

        # Rows
        for hi, h in enumerate(hypotheses):
            key = f"H{hi+1}"
            h_ratings = ratings.get(key, {})
            cells = []
            for e in evidence:
                ekey = f"E{e['id']}"
                r = h_ratings.get(ekey, "?")
                cells.append(r)
            lines.append(f"| **{key}** | {' | '.join(cells)} |")

        return "\n".join(lines)
