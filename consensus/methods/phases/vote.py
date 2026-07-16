"""Vote phase handler for Voting Method.

Each participant votes on all pending motions (for / against / abstain).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str
from ..phase_handler import PhaseHandler
from ._voting_helpers import (
    VALID_VOTES,
    VOTES_TOOL_PARAMETERS,
    extract_votes,
    format_motions_for_voting,
    record_votes,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

# Safety cap: the vote phase advances once every participant has voted on
# every motion, but if some votes can never be parsed (e.g. a model that
# refuses to emit valid JSON) this guarantees the phase still terminates
# instead of stalling the discussion forever.  Each round is one full
# rotation of all participants, so a few rounds is ample headroom.
MAX_VOTE_ROUNDS = 3


class VoteHandler(PhaseHandler):
    """Phase 2: Formal voting on pending motions."""

    phase = Phase(
        name="vote",
        display_name="Voting",
        description=(
            "Vote on each pending motion: for, against, or abstain.  "
            "Include reasoning with your vote."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        motions_text = format_motions_for_voting(state)
        return (
            f"You are {entity.name}, participating in a structured "
            f"deliberation with voting.\n"
            f"Topic: {discussion.topic}\n\n"
            "VOTING PHASE\n\n"
            "Vote on each motion below by calling the submit_votes "
            "tool with one entry per motion: its motion_id, your vote "
            "('for', 'against', or 'abstain'), and your rationale.\n\n"
            f"Motions to vote on:\n{motions_text}"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        motions = state.get("motions", [])
        votes = state.get("votes", [])
        voted_motions = {v["motion_id"] for v in votes
                        if v["entity_id"] == entity.id}
        unvoted = [m for m in motions if m["id"] not in voted_motions]

        if not unvoted:
            return f"{entity.name}, you have already voted on all motions."

        motion_list = "\n".join(
            f"  Motion {m['id']}: {m['text']}" for m in unvoted
        )
        return (
            f"{entity.name}, please cast your vote on each motion by "
            "calling the submit_votes tool, with one entry per motion "
            "(motion_id, vote, rationale).\n\n"
            f"Motions awaiting your vote:\n{motion_list}"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has cast their vote(s).  "
            f"Invite {next_speaker_name} to vote."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        accepted = record_votes(state, entity, extract_votes(content))
        if accepted:
            content += f"\n\n---\n**Votes cast:** {accepted}"
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        state = discussion.method_state
        if not self._pending_motion_ids(entity, state):
            # No unvoted motions: no submit_votes payload could pass
            # validation, so forcing the tool would burn every retry.
            # The free-text path handles the "already voted" prose turn.
            return None
        return OutputToolSpec(
            name="submit_votes",
            description=("Cast your vote (for/against/abstain) with a "
                         "rationale on every pending motion:\n"
                         + format_motions_for_voting(state)),
            parameters=VOTES_TOOL_PARAMETERS,
        )

    @staticmethod
    def _pending_motion_ids(entity: Entity, state: dict) -> set[int]:
        """Motion ids this entity has not voted on yet."""
        voted = {v["motion_id"] for v in state.get("votes", [])
                 if v["entity_id"] == entity.id}
        return {m["id"] for m in state.get("motions", [])} - voted

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_votes payload.

        Rejecting duplicate and already-voted motions here (rather than
        relying on record_votes' silent dedupe) keeps the displayed vote
        lines in process_structured_response consistent with what is
        actually recorded.
        """
        votes = payload.get("votes")
        if not isinstance(votes, list) or not votes:
            return "'votes' must be a non-empty array, one entry per motion."
        state = discussion.method_state
        valid_ids = {m["id"] for m in state.get("motions", [])}
        pending = self._pending_motion_ids(entity, state)
        seen: set[int] = set()
        for v in votes:
            if not isinstance(v, dict):
                return "Each entry in 'votes' must be an object."
            try:
                motion_id = int(v.get("motion_id"))
            except (TypeError, ValueError):
                return "Each vote needs an integer 'motion_id'."
            if motion_id not in valid_ids:
                return (f"Motion {motion_id} does not exist. Valid motion "
                        f"ids: {sorted(valid_ids)}.")
            if motion_id in seen:
                return (f"Motion {motion_id} appears more than once — "
                        "submit exactly one entry per motion.")
            seen.add(motion_id)
            if motion_id not in pending:
                return (f"You have already voted on motion {motion_id}. "
                        f"Vote only on your pending motions: "
                        f"{sorted(pending)}.")
            if str(v.get("vote", "")).lower() not in VALID_VOTES:
                return "Each 'vote' must be 'for', 'against', or 'abstain'."
        return ""

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        votes = [{"motion_id": int(v["motion_id"]),
                  "vote": str(v["vote"]).lower(),
                  "rationale": coerce_str(v, "rationale")}
                 for v in payload["votes"]]
        accepted = record_votes(state, entity, votes)
        lines = [f"**Motion {v['motion_id']} — {v['vote'].upper()}:** "
                 f"{v['rationale']}" for v in votes]
        display = ("\n\n".join(lines)
                   + f"\n\n---\n**Votes cast:** {accepted}")
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when all participants have voted on all motions.

        A deliberation that produced no motions advances immediately —
        there is nothing to vote on.  Falls back to a ``phase_round``
        safety cap so the phase always terminates even if some votes can
        never be recorded, preventing the discussion from stalling
        indefinitely in the voting phase.
        """
        if not discussion.method_state.get("motions"):
            return True
        if _all_votes_in(discussion):
            return True
        phase_round = discussion.method_state.get("phase_round", 1)
        if phase_round > MAX_VOTE_ROUNDS:
            logger.warning(
                "Vote phase reached round %d without all votes recorded; "
                "advancing with %d vote(s) collected.",
                phase_round,
                len(discussion.method_state.get("votes", [])),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        motions = state.get("motions", [])
        if not motions:
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "No motions were proposed during deliberation.  "
                "The moderator will synthesise the discussion."
            )
        motions_text = format_motions_for_voting(state)
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"Deliberation is complete.  {len(motions)} motion(s) are "
            f"on the table:\n{motions_text}\n\n"
            "Each participant will now vote on every motion "
            "(for / against / abstain) with reasoning."
        )


def _all_votes_in(discussion: Discussion) -> bool:
    """Check if all participants have voted on all motions."""
    state = discussion.method_state
    motions = state.get("motions", [])
    votes = state.get("votes", [])

    if not motions:
        return False

    participant_ids = set(discussion.turn_order)

    for motion in motions:
        mid = motion["id"]
        voters = {v["entity_id"] for v in votes if v["motion_id"] == mid}
        if not participant_ids.issubset(voters):
            return False

    return True
