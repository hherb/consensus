"""Participant Voting — structured deliberation with formal motions and votes.

Participants deliberate, propose motions, vote, and see results tallied.
Supports multiple motion types and configurable vote thresholds.

Phases:
  1. DELIBERATE  — Open discussion; participants may propose motions
                   via JSON blocks in their responses
  2. VOTE        — Each participant votes on all pending motions
                   (for / against / abstain)
  3. TALLY       — Moderator announces results and synthesises
                   the outcome
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity

logger = logging.getLogger(__name__)

# Valid vote values
VALID_VOTES = {"for", "against", "abstain"}

# Default deliberation rounds before voting
DEFAULT_DELIBERATION_ROUNDS = 2


class VotingMethod(DiscussionMethod):
    """Participant voting with formal motions and tallied results."""

    name = "voting"
    display_name = "Participant Voting"
    description = (
        "Structured deliberation followed by formal voting.  Participants "
        "discuss the topic, propose motions during deliberation, then vote "
        "on each motion (for / against / abstain).  Results are tallied "
        "with configurable thresholds (simple majority by default).  "
        "Best for decisions requiring clear group consensus."
    )
    default_phases = (
        Phase(
            name="deliberate",
            display_name="Deliberation",
            description=(
                "Discuss the topic and propose motions for the group to "
                "vote on.  Include a JSON block to propose a motion."
            ),
            rounds=DEFAULT_DELIBERATION_ROUNDS,
        ),
        Phase(
            name="vote",
            display_name="Voting",
            description=(
                "Vote on each pending motion: for, against, or abstain.  "
                "Include reasoning with your vote."
            ),
            rounds=1,
        ),
        Phase(
            name="tally",
            display_name="Results & Synthesis",
            description=(
                "The moderator announces vote results and synthesises "
                "the group's decision."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Initialize voting state."""
        state = super().init_state(discussion)
        state["motions"] = []  # [{id, text, proposed_by, motion_type}]
        state["votes"] = []  # [{entity_id, motion_id, vote, rationale}]
        state["next_motion_id"] = 1
        state["threshold"] = "simple_majority"  # simple_majority, supermajority, unanimous
        return state

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        """Determine whether to advance to the next phase."""
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "deliberate":
            # Advance after deliberation rounds complete
            return state.get("phase_round", 1) > phase.rounds

        if phase.name == "vote":
            # Advance when all participants have voted on all motions
            return self._all_votes_in(discussion)

        if phase.name == "tally":
            return state.get("phase_round", 1) > 1

        return False

    def _all_votes_in(self, discussion: Discussion) -> bool:
        """Check if all participants have voted on all motions."""
        state = discussion.method_state
        motions = state.get("motions", [])
        votes = state.get("votes", [])

        if not motions:
            return False

        # Get participant IDs (exclude moderator)
        participant_ids = set(discussion.turn_order)

        for motion in motions:
            mid = motion["id"]
            voters = {v["entity_id"] for v in votes if v["motion_id"] == mid}
            if not participant_ids.issubset(voters):
                return False

        return True

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return phase-appropriate system prompt."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state

        base = (
            f"You are {entity.name}, participating in a structured "
            f"deliberation with voting.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "deliberate":
            motions_text = self._format_motions(state)
            return base + (
                "DELIBERATION PHASE\n\n"
                "Discuss the topic and, when ready, propose motions for "
                "the group to vote on.  To propose a motion, include a "
                "JSON block in your response:\n"
                "```json\n"
                '{"motion": "Your proposed motion text"}\n'
                "```\n\n"
                "You may propose multiple motions across your turns.  "
                "Focus on clear, actionable proposals.\n\n"
                f"Current motions on the table:\n{motions_text}"
            )

        if phase.name == "vote":
            motions_text = self._format_motions_for_voting(state)
            return base + (
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

        if phase.name == "tally":
            return ""  # moderator handles tally

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return turn-specific instruction."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "deliberate":
            return (
                f"It is your turn, {entity.name}.  Share your thoughts "
                "on the topic.  If you have a clear proposal, include a "
                "motion in a JSON block."
            )

        if phase.name == "vote":
            state = discussion.method_state
            motions = state.get("motions", [])
            # Check which motions this entity hasn't voted on yet
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

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return summary prompt."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "deliberate":
            state = discussion.method_state
            n_motions = len(state.get("motions", []))
            return (
                f"{speaker_name} has spoken.  "
                f"There are currently {n_motions} motion(s) on the table.  "
                f"Briefly summarise the key points and invite "
                f"{next_speaker_name} to continue the deliberation."
            )

        if phase.name == "vote":
            return (
                f"{speaker_name} has cast their vote(s).  "
                f"Invite {next_speaker_name} to vote."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return conclusion prompt with vote tallies."""
        state = discussion.method_state
        tally = self._tally_votes(discussion)
        motions = state.get("motions", [])
        threshold = state.get("threshold", "simple_majority")

        tally_text = self._format_tally(tally, motions, discussion)

        return (
            "The voting process is complete.\n\n"
            f"Vote results:\n{tally_text}\n\n"
            f"Threshold: {threshold.replace('_', ' ')}\n\n"
            "Provide a synthesis:\n"
            "1. **Results** — State which motions passed and which failed\n"
            "2. **Analysis** — Analyse the voting patterns and rationale\n"
            "3. **Consensus assessment** — How strong is the group's "
            "agreement?  Were there significant dissents?\n"
            "4. **Recommendations** — Based on the vote outcomes, what "
            "are the next steps?\n\n"
            "Present the results clearly and cite specific reasoning "
            "from participants."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        """Announce phase transitions."""
        state = discussion.method_state

        if new_phase.name == "vote":
            motions = state.get("motions", [])
            if not motions:
                return (
                    f"**Phase: {new_phase.display_name}**\n\n"
                    "No motions were proposed during deliberation.  "
                    "The moderator will synthesise the discussion."
                )
            motions_text = self._format_motions_for_voting(state)
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"Deliberation is complete.  {len(motions)} motion(s) are "
                f"on the table:\n{motions_text}\n\n"
                "Each participant will now vote on every motion "
                "(for / against / abstain) with reasoning."
            )

        if new_phase.name == "tally":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All votes are in.  The moderator will now tally the "
                "results and present the outcome."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract motions (deliberate phase) or votes (vote phase)."""
        phase = self.current_phase(discussion)
        if not phase:
            return ProcessedResponse(display_content=content)

        state = discussion.method_state
        extracted: dict[str, Any] = {}

        if phase.name == "deliberate":
            motions = self._extract_motions(content)
            for motion_text in motions:
                motion_id = state.get("next_motion_id", 1)
                state["motions"].append({
                    "id": motion_id,
                    "text": motion_text,
                    "proposed_by": entity.name,
                })
                state["next_motion_id"] = motion_id + 1
                extracted["motions"] = motions

            if motions:
                motion_lines = "\n".join(
                    f"  - Motion {state['motions'][-len(motions)+i]['id']}: {m}"
                    for i, m in enumerate(motions)
                )
                content += f"\n\n---\n**Motions proposed:**\n{motion_lines}"

        elif phase.name == "vote":
            votes = self._extract_votes(content)
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

            extracted["votes_cast"] = accepted
            if accepted:
                content += f"\n\n---\n**Votes cast:** {accepted}"

        return ProcessedResponse(
            display_content=content,
            extracted_data=extracted,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_motions(self, content: str) -> list[str]:
        """Parse motion proposals from JSON blocks in the response."""
        motions: list[str] = []

        for match in re.finditer(r'```(?:json)?\s*(\{[^`]+\})\s*```',
                                  content, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                if "motion" in data and isinstance(data["motion"], str):
                    motions.append(data["motion"].strip())
            except (json.JSONDecodeError, TypeError):
                continue

        return motions

    def _extract_votes(self, content: str) -> list[dict]:
        """Parse vote JSON blocks from the response."""
        votes: list[dict] = []

        for match in re.finditer(r'```(?:json)?\s*(\{[^`]+\})\s*```',
                                  content, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                if "vote" in data and "motion_id" in data:
                    votes.append(data)
            except (json.JSONDecodeError, TypeError):
                continue

        return votes

    def _tally_votes(self, discussion: Discussion) -> dict[int, dict[str, int]]:
        """Tally votes per motion.

        Returns:
            Dict mapping motion_id -> {"for": N, "against": N, "abstain": N}.
        """
        state = discussion.method_state
        motions = state.get("motions", [])
        votes = state.get("votes", [])

        tally: dict[int, dict[str, int]] = {}
        for motion in motions:
            mid = motion["id"]
            tally[mid] = {"for": 0, "against": 0, "abstain": 0}

        for vote in votes:
            mid = vote["motion_id"]
            val = vote["vote"]
            if mid in tally and val in tally[mid]:
                tally[mid][val] += 1

        return tally

    def _format_motions(self, state: dict) -> str:
        """Format current motions for display."""
        motions = state.get("motions", [])
        if not motions:
            return "  (No motions proposed yet)"
        return "\n".join(
            f"  Motion {m['id']}: \"{m['text']}\" (proposed by {m['proposed_by']})"
            for m in motions
        )

    def _format_motions_for_voting(self, state: dict) -> str:
        """Format motions with IDs for the voting phase."""
        motions = state.get("motions", [])
        if not motions:
            return "  (No motions to vote on)"
        return "\n".join(
            f"  Motion {m['id']}: \"{m['text']}\""
            for m in motions
        )

    def _format_tally(self, tally: dict[int, dict[str, int]],
                      motions: list[dict],
                      discussion: Discussion) -> str:
        """Format vote tally for display."""
        n_voters = len(discussion.turn_order)
        threshold = discussion.method_state.get("threshold", "simple_majority")

        lines = []
        for motion in motions:
            mid = motion["id"]
            counts = tally.get(mid, {"for": 0, "against": 0, "abstain": 0})
            total = counts["for"] + counts["against"]

            if threshold == "unanimous":
                passed = counts["against"] == 0 and counts["for"] > 0
            elif threshold == "supermajority":
                passed = total > 0 and counts["for"] / total >= 2 / 3
            else:  # simple_majority
                passed = counts["for"] > counts["against"]

            status = "PASSED" if passed else "FAILED"

            lines.append(
                f"  Motion {mid}: \"{motion['text']}\"\n"
                f"    For: {counts['for']}  |  Against: {counts['against']}  |  "
                f"Abstain: {counts['abstain']}  →  **{status}**"
            )

        return "\n".join(lines)
