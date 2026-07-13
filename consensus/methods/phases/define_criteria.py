"""Define Criteria phase handler for Adversarial Collaboration.

Participants jointly define the specific, concrete criteria that would
settle the disagreement.  Criteria are locked before evidence gathering.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

# Give up and advance after this many rounds even without parsed
# criteria — an unparseable group must not loop forever (issue #15).
MAX_CRITERIA_ROUNDS = 4

#: Minimum length (exclusive) a parsed/submitted criterion string must
#: exceed to count as substantive — filters out stray short fragments
#: like list markers or truncated matches.  Shared by the free-text
#: parser (``_parse_criteria``) and the structured-output validator
#: (``validate_criteria_payload``) so both paths hold criteria to the
#: same bar.
CRITERION_MIN_LENGTH = 10

#: JSON Schema for the submit_criteria output tool (issue #23).
CRITERIA_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("The proposed settlement criteria: specific, "
                            "concrete, testable conditions that would "
                            "resolve the disagreement."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale for these criteria: why they "
                            "are specific, measurable, and fair to both "
                            "sides."),
        },
    },
    "required": ["criteria", "reasoning"],
}


def validate_criteria_payload(payload: dict) -> str:
    """Return '' if a submit_criteria payload is usable, else an error.

    Mirrors the free-text path's substantive-length filter
    (``CRITERION_MIN_LENGTH``) and ``validate_estimate_payload``'s
    wording style.
    """
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return "'criteria' must be a non-empty array of criterion strings."
    for c in criteria:
        if not isinstance(c, str) or len(c.strip()) <= CRITERION_MIN_LENGTH:
            return (
                "Each criterion must be a substantive string longer than "
                f"{CRITERION_MIN_LENGTH} characters describing a specific, "
                f"testable condition (got: {c!r})."
            )
    if not str(payload.get("reasoning", "")).strip():
        return "'reasoning' must contain your rationale for these criteria."
    return ""


class DefineCriteriaHandler(PhaseHandler):
    """Phase 2: Jointly define settlement criteria."""

    phase = Phase(
        name="criteria",
        display_name="Define Settlement Criteria",
        description=(
            "Participants jointly define the specific, concrete "
            "criteria that would settle the disagreement.  What "
            "evidence, if found, would change your mind?  Both sides "
            "must agree on the criteria BEFORE examining evidence."
        ),
        rounds=2,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"criteria": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        base = (
            f"You are {entity.name}, participating in an Adversarial "
            f"Collaboration.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        positions = state.get("positions", {})
        pos_text = "\n".join(
            f"  - {eid}: {pos}"
            for eid, pos in positions.items()
        )
        return base + (
            "CRITERIA DEFINITION PHASE\n\n"
            f"Positions stated:\n{pos_text}\n\n"
            "Now you must jointly define the SETTLEMENT CRITERIA — "
            "specific, concrete, testable conditions that would "
            "resolve the disagreement.\n\n"
            "For each criterion, specify:\n"
            "1. The criterion itself (specific and measurable)\n"
            "2. What finding would support Position A\n"
            "3. What finding would support Position B\n\n"
            "Submit your criteria by calling the submit_criteria tool "
            "with an array of criterion strings — each one a complete, "
            "testable statement covering all three points above — plus "
            "your rationale in the 'reasoning' field.\n\n"
            "CRITICAL: These criteria will be LOCKED before evidence "
            "gathering.  You cannot change them later.  Design criteria "
            "that you believe are fair to BOTH sides."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("phase_round", 1)
        if round_num == 1:
            return (
                f"It is your turn, {entity.name}.  Propose settlement "
                "criteria that would be fair to both sides by calling "
                "the submit_criteria tool."
            )
        return (
            f"Round {round_num}, {entity.name}.  Review the proposed "
            "criteria and suggest refinements.  Do you accept these "
            "criteria as fair?  Call the submit_criteria tool with the "
            "refined (or unchanged) set of criteria and your reasoning."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed/refined settlement criteria.  "
            "Note which criteria seem acceptable to both sides and "
            f"which need further negotiation.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All positions are on the table.  Participants will now "
            "jointly define specific, testable criteria that would "
            "settle the disagreement.  These criteria will be LOCKED "
            "before evidence gathering begins."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_criteria = self._parse_criteria(content)
        if new_criteria:
            existing = state.get("criteria", [])
            for c in new_criteria:
                if c not in existing:
                    existing.append(c)
            state["criteria"] = existing
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        """Declare the forced submit_criteria tool for this phase."""
        return OutputToolSpec(
            name="submit_criteria",
            description=("Submit the proposed settlement criteria as an "
                         "array of criterion strings, plus your "
                         "reasoning."),
            parameters=CRITERIA_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_criteria payload via the shared function."""
        return validate_criteria_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Dedup and append submitted criteria, then render the display.

        Mirrors ``process_response``'s exact-membership dedup against
        ``state["criteria"]``.  The display renders the reasoning first,
        followed by a numbered list of the criteria submitted this turn
        (matching ``_belief_helpers``-style display conventions).
        """
        state = discussion.method_state
        submitted = [str(c).strip() for c in payload["criteria"]]
        existing = state.get("criteria", [])
        for c in submitted:
            if c not in existing:
                existing.append(c)
        state["criteria"] = existing

        reasoning = str(payload.get("reasoning", "")).strip()
        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(submitted, 1))
        display = f"{reasoning}\n\n{numbered}" if reasoning else numbered
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_criteria(self, content: str) -> list[str]:
        """Extract criteria from content."""
        patterns = [
            r'\*\*C\d+:\*\*\s*(.+?)(?=\n\s*[-*]|\n\*\*C\d+|\n\n|$)',
            r'^\s*C\d+[\.\):]\s*(.+)',
            r'^\s*\d+[\.\)]\s*(.+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
            if matches:
                return [m.strip().rstrip('.') for m in matches
                        if len(m.strip()) > CRITERION_MIN_LENGTH]
        return []

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_CRITERIA_ROUNDS:
            return True
        return (bool(state.get("criteria"))
                and phase_round > self.phase.rounds)
