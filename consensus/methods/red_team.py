"""Red Team / Blue Team with Rotation — structured adversarial analysis.

Each round, one participant is designated as the Red Team (attacker)
while the others form the Blue Team (constructors).  The Red Team sees
only the current conclusion and tries to break it — they do NOT
participate in construction.  The Red Team role rotates each round.

Phases:
  1. CONSTRUCT  — Blue Team builds an initial position (Red Team silent)
  2. ATTACK     — Red Team attacks the position; Blue Team defends
  3. REVISE     — Blue Team revises based on surviving attacks
  4. ASSESS     — Moderator evaluates what survived scrutiny
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class RedTeamBlueTeam(DiscussionMethod):
    """Red Team / Blue Team with rotating adversarial role."""

    name = "red_team"
    display_name = "Red Team / Blue Team"
    description = (
        "Rotating adversarial analysis.  Each round, one participant "
        "is designated as the Red Team (attacker) while the others "
        "construct and defend a position.  The Red Team role rotates, "
        "ensuring every perspective gets stress-tested."
    )
    default_phases = (
        Phase(
            name="construct",
            display_name="Construction",
            description=(
                "Blue Team members build an initial position or analysis.  "
                "The designated Red Team member is silent during this phase."
            ),
            rounds=1,
        ),
        Phase(
            name="attack",
            display_name="Red Team Attack",
            description=(
                "The Red Team member attacks the constructed position, "
                "identifying weaknesses, logical flaws, missing evidence, "
                "and alternative explanations.  Blue Team then defends."
            ),
            rounds=2,
        ),
        Phase(
            name="revise",
            display_name="Revision",
            description=(
                "Blue Team revises the position based on attacks that "
                "could not be adequately defended."
            ),
            rounds=1,
        ),
        Phase(
            name="assess",
            display_name="Assessment",
            description=(
                "The moderator evaluates which attacks succeeded, what "
                "was revised, and how robust the final position is."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        state = super().init_state(discussion)
        state["red_team_entity_id"] = None  # current red team member
        state["red_team_rotation"] = 0      # which rotation we're on
        state["attacks"] = []               # list of attack summaries
        return state

    # ------------------------------------------------------------------
    # Turn ordering
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """In construct/revise phases, exclude the Red Team member.
        In attack phase, Red Team goes first, then Blue Team defends."""
        phase = self.current_phase(discussion)
        if not phase:
            return entity_ids
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")

        if red_id is None:
            # Assign the first Red Team member
            if entity_ids:
                red_id = entity_ids[0]
                state["red_team_entity_id"] = red_id

        if phase.name in ("construct", "revise"):
            # Exclude Red Team
            return [eid for eid in entity_ids if eid != red_id]

        if phase.name == "attack":
            # Red Team first, then Blue Team defends
            blue = [eid for eid in entity_ids if eid != red_id]
            return [red_id] + blue if red_id in entity_ids else entity_ids

        return entity_ids

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")
        is_red = (entity.id == red_id)

        base = (
            f"You are {entity.name}, participating in a Red Team / Blue "
            f"Team analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "construct":
            if is_red:
                return base + (
                    "You are the RED TEAM this round.  You are SILENT "
                    "during the construction phase.  Wait for your turn "
                    "to attack."
                )
            return base + (
                "CONSTRUCTION PHASE — You are BLUE TEAM.\n\n"
                "Build a well-reasoned position or analysis on the topic.  "
                "Present your best arguments, evidence, and reasoning.  "
                "The Red Team cannot see or participate in construction — "
                "they will only see your final position."
            )

        if phase.name == "attack":
            if is_red:
                return base + (
                    "RED TEAM ATTACK PHASE\n\n"
                    "You are the Red Team.  Your ONLY job is DESTRUCTION, "
                    "not construction.  Attack the Blue Team's position:\n\n"
                    "1. **Logical flaws** — Find contradictions, non "
                    "sequiturs, circular reasoning\n"
                    "2. **Missing evidence** — What did they fail to "
                    "consider?\n"
                    "3. **Alternative explanations** — What other "
                    "conclusions fit the same evidence?\n"
                    "4. **Weak assumptions** — What are they taking for "
                    "granted?\n"
                    "5. **Edge cases** — Where does their position break "
                    "down?\n\n"
                    "Be aggressive and thorough.  You succeed by finding "
                    "genuine weaknesses, not by being contrarian."
                )
            return base + (
                "DEFENSE PHASE — You are BLUE TEAM.\n\n"
                "The Red Team has attacked your position.  Defend against "
                "the attacks:\n"
                "- For attacks you can rebut: provide counter-evidence or "
                "reasoning\n"
                "- For attacks you cannot rebut: acknowledge the weakness "
                "honestly\n\n"
                "Do NOT dismiss attacks without substantive response."
            )

        if phase.name == "revise":
            if is_red:
                return base + (
                    "You are the RED TEAM.  You are SILENT during revision.  "
                    "The Blue Team is revising their position based on your "
                    "attacks."
                )
            return base + (
                "REVISION PHASE — You are BLUE TEAM.\n\n"
                "Revise your position based on the Red Team attacks that "
                "could not be adequately defended.  Specifically:\n"
                "1. What has changed in your position?\n"
                "2. What attacks were you unable to defend?\n"
                "3. What remains unchanged and why?\n\n"
                "Be honest about weaknesses exposed."
            )

        if phase.name == "assess":
            return ""  # moderator handles assessment

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")
        is_red = (entity.id == red_id)

        if phase.name == "construct":
            return (
                f"Blue Team member {entity.name}: present your analysis "
                "and position on the topic."
            )

        if phase.name == "attack":
            if is_red:
                return (
                    f"Red Team ({entity.name}): attack the Blue Team's "
                    "position.  Find every weakness you can."
                )
            return (
                f"Blue Team ({entity.name}): defend against the Red "
                "Team's attacks."
            )

        if phase.name == "revise":
            return (
                f"Blue Team ({entity.name}): revise the position based "
                "on attacks that succeeded."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "construct":
            return (
                f"{speaker_name} has presented their contribution to the "
                "position.  Note the key arguments and evidence.  "
                f"Next: {next_speaker_name}."
            )

        if phase.name == "attack":
            return (
                f"{speaker_name} has made their contribution (attack or "
                "defense).  Note the strongest points raised.  "
                f"Next: {next_speaker_name}."
            )

        if phase.name == "revise":
            return (
                f"{speaker_name} has presented their revisions.  Note what "
                f"changed and what remained.  Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        return (
            "The Red Team / Blue Team analysis is complete.\n\n"
            "Provide a comprehensive assessment:\n"
            "1. **Successful attacks** — Which Red Team attacks could not "
            "be adequately defended?  These represent genuine weaknesses.\n"
            "2. **Failed attacks** — Which attacks were successfully "
            "rebutted?  These confirm strengths.\n"
            "3. **Position evolution** — How did the Blue Team's position "
            "change from initial construction to final revision?\n"
            "4. **Surviving claims** — What elements of the position "
            "survived Red Team scrutiny?\n"
            "5. **Residual vulnerabilities** — What weaknesses remain "
            "even after revision?\n"
            "6. **Robustness rating** — How robust is the final position?  "
            "Rate as: STRONG (survived most attacks), MODERATE (significant "
            "revisions needed), or WEAK (fundamental problems exposed).\n\n"
            "Base your assessment on the specific attacks and defenses "
            "presented."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        state = discussion.method_state
        red_id = state.get("red_team_entity_id")

        if new_phase.name == "attack":
            # Find the red team entity name from the discussion
            red_name = f"Entity {red_id}"
            for e in discussion.entities:
                if e.id == red_id:
                    red_name = e.name
                    break
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"**{red_name}** is the Red Team this round.  "
                "They will attack the Blue Team's position, then the "
                "Blue Team will defend."
            )

        if new_phase.name == "revise":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "The attack/defense round is over.  Blue Team members "
                "will now revise their position, incorporating attacks "
                "they could not adequately defend."
            )

        if new_phase.name == "assess":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "Revision is complete.  The moderator will now assess "
                "the robustness of the final position."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state
        return state.get("phase_round", 1) > phase.rounds
