"""Multi-voting phase handler for Nominal Group Technique.

Each participant distributes a fixed pool of points across the
candidate ideas via the forced ``submit_points`` output tool
(issue #23); a JSON-block / ``Candidate N: X points`` free-text parse
remains the human/fallback path.  The phase advances when every
participant has allocated, with a round cap so unparseable turns
cannot stall the discussion (see vote.py's MAX_VOTE_ROUNDS pattern).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._delphi_helpers import anonymise_content
from ._ngt_helpers import (
    ALLOCATIONS_TOOL_PARAMETERS,
    MAX_ALLOCATE_ROUNDS,
    POINTS_PER_VOTER,
    entities_with_allocations,
    extract_allocations,
    format_candidates,
    record_allocations,
    validate_allocations_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class AllocatePointsHandler(PhaseHandler):
    """Phase 4: Participants distribute points across candidates."""

    phase = Phase(
        name="allocate",
        display_name="Multi-Voting",
        description=(
            "Each participant distributes a fixed pool of points "
            "across the candidate ideas to express their priorities."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"point_allocations": [],
                "points_per_voter": POINTS_PER_VOTER}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        candidates_text = format_candidates(state)
        return (
            f"You are {entity.name}, participating in a Nominal Group "
            "Technique (NGT) session.\n"
            f"Topic: {discussion.topic}\n\n"
            "MULTI-VOTING PHASE\n\n"
            f"Distribute exactly {pool} points across the candidate "
            "ideas below to express your priorities.  You may give one "
            "candidate everything or spread points widely.\n\n"
            "Cast your allocation by calling the submit_points tool "
            "with one entry per candidate you support (candidate_id, "
            "points, optional rationale) plus your overall reasoning.\n\n"
            f"Candidate ideas:\n{candidates_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        if entity.id in entities_with_allocations(state):
            return (f"{entity.name}, you have already allocated your "
                    "points.")
        return (
            f"It is your turn, {entity.name}.  Distribute exactly "
            f"{pool} points across the candidates by calling the "
            "submit_points tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has allocated their points.  Invite "
            f"{next_speaker_name} to allocate theirs."
        )

    # ------------------------------------------------------------------
    # Context filtering — keep authorship hidden until results
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
        accepted = record_allocations(state, entity,
                                      extract_allocations(content))
        if accepted:
            content += (f"\n\n---\n**Point allocations recorded:** "
                        f"{accepted}")
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        state = discussion.method_state
        if not state.get("candidates"):
            # Nothing to vote on: no payload could pass validation, so
            # forcing the tool would burn every retry.
            return None
        if entity.id in entities_with_allocations(state):
            # Already allocated: the free-text path handles the
            # "already voted" prose turn (see vote.py).
            return None
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        return OutputToolSpec(
            name="submit_points",
            description=(f"Distribute exactly {pool} points across the "
                         "candidate ideas:\n" + format_candidates(state)),
            parameters=ALLOCATIONS_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        valid_ids = {c["id"] for c in state.get("candidates", [])}
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        return validate_allocations_payload(payload, valid_ids, pool)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        allocations = [{"candidate_id": int(a["candidate_id"]),
                        "points": int(a["points"]),
                        "rationale": str(a.get("rationale") or "")}
                       for a in payload["allocations"]]
        accepted = record_allocations(state, entity, allocations)
        titles = {c["id"]: c["title"] for c in state.get("candidates", [])}
        lines = []
        for a in allocations:
            line = (f"**Candidate {a['candidate_id']} "
                    f"({titles.get(a['candidate_id'], '?')}): "
                    f"{a['points']} point(s)**")
            if a["rationale"]:
                line += f" — {a['rationale']}"
            lines.append(line)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = (reasoning + "\n\n" + "\n".join(lines)
                   + f"\n\n---\n**Point allocations recorded:** {accepted}")
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when every participant has allocated their points.

        A clustering that produced no candidates advances immediately —
        there is nothing to vote on.  Falls back to a ``phase_round``
        cap so the phase always terminates even if some allocations can
        never be recorded.
        """
        state = discussion.method_state
        if not state.get("candidates"):
            return True
        participant_ids = set(discussion.turn_order)
        if participant_ids and participant_ids.issubset(
                entities_with_allocations(state)):
            return True
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_ALLOCATE_ROUNDS:
            logger.warning(
                "Multi-voting reached round %d without all allocations; "
                "advancing with %d allocation(s) recorded.",
                phase_round, len(state.get("point_allocations", [])),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        pool = state.get("points_per_voter", POINTS_PER_VOTER)
        candidates_text = format_candidates(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "Clarification is complete.  Each participant now "
            f"distributes exactly {pool} points across the "
            f"candidates:\n{candidates_text}"
        )
