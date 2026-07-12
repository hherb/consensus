"""Vote phase handler for Voting Method.

Each participant votes on all pending motions (for / against / abstain).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._voting_helpers import (
    VALID_VOTES,
    extract_votes,
    format_motions_for_voting,
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
            "Vote on each motion below.  For EACH motion, include a "
            "JSON block with your vote:\n"
            "```json\n"
            '{"vote": "for|against|abstain", "motion_id": <number>}\n'
            "```\n\n"
            "Valid votes: 'for', 'against', 'abstain'\n\n"
            "Provide reasoning for each vote after the JSON block.\n\n"
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
            f"{entity.name}, please cast your vote on each motion.\n\n"
            "CRITICAL: For EACH motion, include a JSON block as the "
            "FIRST thing, before your reasoning:\n"
            "```json\n"
            '{"vote": "for|against|abstain", "motion_id": <number>}\n'
            "```\n\n"
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
        votes = extract_votes(content)
        valid_motion_ids = {m["id"] for m in state.get("motions", [])}
        accepted = 0

        for vote_data in votes:
            vote_val = vote_data.get("vote", "").lower()
            motion_id = vote_data.get("motion_id")

            if vote_val not in VALID_VOTES:
                logger.warning(
                    "Invalid vote value '%s' from %s, skipping",
                    vote_val, entity.name,
                )
                continue
            # Motion ids are stored as ints; models sometimes emit them as
            # JSON strings ("1").  Coerce so the membership test below does
            # not silently drop an otherwise-valid vote.
            try:
                motion_id = int(motion_id)
            except (TypeError, ValueError):
                logger.warning(
                    "Vote with non-numeric motion_id %r from %s, skipping",
                    motion_id, entity.name,
                )
                continue
            if motion_id not in valid_motion_ids:
                logger.warning(
                    "Vote for unknown motion %s from %s, skipping",
                    motion_id, entity.name,
                )
                continue
            # Prevent double-voting
            already_voted = any(
                v["entity_id"] == entity.id and v["motion_id"] == motion_id
                for v in state.get("votes", [])
            )
            if already_voted:
                logger.info(
                    "%s already voted on motion %d, skipping duplicate",
                    entity.name, motion_id,
                )
                continue

            state.setdefault("votes", []).append({
                "entity_id": entity.id,
                "entity_name": entity.name,
                "motion_id": motion_id,
                "vote": vote_val,
                "rationale": vote_data.get("rationale", ""),
            })
            accepted += 1

        extracted = {"votes_cast": accepted}
        if accepted:
            content += f"\n\n---\n**Votes cast:** {accepted}"

        return ProcessedResponse(
            display_content=content,
            extracted_data=extracted,
        )

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
