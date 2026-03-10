"""Adversarial Collaboration (Kahneman-style) — structured disagreement.

Participants who genuinely disagree jointly design the criteria that
would settle the question BEFORE gathering evidence.  This prevents
post-hoc rationalisation and moves-the-goalposts fallacies.

Phases:
  1. POSITIONS    — Each participant states their position and reasoning
  2. CRITERIA     — Participants jointly define what evidence would
                    settle the question (ideally before seeing evidence)
  3. EVIDENCE     — Gather evidence according to the agreed criteria
  4. ADJUDICATE   — Moderator evaluates the evidence against the
                    pre-agreed criteria and declares a verdict
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class AdversarialCollaboration(DiscussionMethod):
    """Adversarial Collaboration — agree on criteria before evidence."""

    name = "adversarial_collab"
    display_name = "Adversarial Collaboration"
    description = (
        "Participants who disagree jointly design the criteria that "
        "would settle the question BEFORE gathering evidence.  Prevents "
        "post-hoc rationalisation and goalpost-shifting.  Inspired by "
        "Daniel Kahneman's adversarial collaboration methodology."
    )
    default_phases = (
        Phase(
            name="positions",
            display_name="State Positions",
            description=(
                "Each participant states their position on the question "
                "and their strongest reasons for holding it."
            ),
            rounds=1,
        ),
        Phase(
            name="criteria",
            display_name="Define Settlement Criteria",
            description=(
                "Participants jointly define the specific, concrete "
                "criteria that would settle the disagreement.  What "
                "evidence, if found, would change your mind?  Both sides "
                "must agree on the criteria BEFORE examining evidence."
            ),
            rounds=2,
        ),
        Phase(
            name="evidence",
            display_name="Evidence Gathering",
            description=(
                "Gather evidence relevant to the agreed criteria.  "
                "Focus specifically on the criteria — not on general "
                "arguments for your position."
            ),
            rounds=2,
            allow_tools=True,
        ),
        Phase(
            name="adjudicate",
            display_name="Adjudication",
            description=(
                "The moderator evaluates the evidence against the "
                "pre-agreed criteria and declares a verdict."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        state = super().init_state(discussion)
        state["positions"] = {}  # {entity_id: position_summary}
        state["criteria"] = []   # list of agreed criteria strings
        return state

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state

        base = (
            f"You are {entity.name}, participating in an Adversarial "
            f"Collaboration.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "positions":
            return base + (
                "POSITION STATEMENT PHASE\n\n"
                "State your position on the question clearly and concisely.  "
                "Include:\n"
                "1. Your position (what you believe to be true)\n"
                "2. Your 2-3 strongest reasons\n"
                "3. What you think the opposing view gets wrong\n\n"
                "Be honest and direct — the goal is to make genuine "
                "disagreements explicit so they can be resolved."
            )

        if phase.name == "criteria":
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
                "Format:\n"
                "**C1:** <criterion>\n"
                "  - If <finding A> → supports Position A\n"
                "  - If <finding B> → supports Position B\n\n"
                "CRITICAL: These criteria will be LOCKED before evidence "
                "gathering.  You cannot change them later.  Design criteria "
                "that you believe are fair to BOTH sides."
            )

        if phase.name == "evidence":
            criteria = state.get("criteria", [])
            criteria_text = "\n".join(
                f"  C{i+1}: {c}" for i, c in enumerate(criteria)
            )
            return base + (
                "EVIDENCE GATHERING PHASE\n\n"
                f"Agreed settlement criteria:\n{criteria_text}\n\n"
                "Find evidence specifically relevant to these criteria.  "
                "Use tools to search for facts, data, and studies.\n\n"
                "For each piece of evidence:\n"
                "1. State the evidence clearly\n"
                "2. Cite the source\n"
                "3. Indicate which criterion it addresses (C1, C2, etc.)\n"
                "4. State what it supports\n\n"
                "Be honest — report evidence even if it undermines your "
                "position.  The criteria were designed to be fair."
            )

        if phase.name == "adjudicate":
            return ""  # moderator handles adjudication

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "positions":
            return (
                f"It is your turn, {entity.name}.  State your position "
                "on this question clearly, with your strongest reasons."
            )

        if phase.name == "criteria":
            round_num = discussion.method_state.get("phase_round", 1)
            if round_num == 1:
                return (
                    f"It is your turn, {entity.name}.  Propose settlement "
                    "criteria that would be fair to both sides."
                )
            return (
                f"Round {round_num}, {entity.name}.  Review the proposed "
                "criteria and suggest refinements.  Do you accept these "
                "criteria as fair?"
            )

        if phase.name == "evidence":
            round_num = discussion.method_state.get("phase_round", 1)
            return (
                f"Evidence round {round_num}, {entity.name}.  Find and "
                "present evidence relevant to the agreed criteria.  "
                "Use your tools."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "positions":
            return (
                f"{speaker_name} has stated their position.  Note the "
                "key points of agreement and disagreement with previous "
                f"positions.  Next: {next_speaker_name}."
            )

        if phase.name == "criteria":
            return (
                f"{speaker_name} has proposed/refined settlement criteria.  "
                "Note which criteria seem acceptable to both sides and "
                f"which need further negotiation.  Next: {next_speaker_name}."
            )

        if phase.name == "evidence":
            return (
                f"{speaker_name} has presented evidence.  Note which "
                "criteria it addresses and which position it supports.  "
                f"Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        positions = state.get("positions", {})
        criteria = state.get("criteria", [])

        pos_text = "\n".join(
            f"  - {eid}: {pos}"
            for eid, pos in positions.items()
        )
        criteria_text = "\n".join(
            f"  C{i+1}: {c}" for i, c in enumerate(criteria)
        )

        return (
            "The Adversarial Collaboration is complete.\n\n"
            f"Positions:\n{pos_text}\n\n"
            f"Pre-agreed settlement criteria:\n{criteria_text}\n\n"
            "Provide a comprehensive adjudication:\n"
            "1. **Criterion-by-criterion verdict** — For each criterion, "
            "what does the evidence show?  Which position does it support?\n"
            "2. **Overall verdict** — Based on the pre-agreed criteria, "
            "which position is better supported?  By how much?\n"
            "3. **Unresolved criteria** — Were any criteria impossible to "
            "evaluate with the available evidence?\n"
            "4. **Areas of genuine uncertainty** — Where does the evidence "
            "remain ambiguous or insufficient?\n"
            "5. **Synthesis** — Is there a more nuanced position that "
            "integrates the strongest elements of both sides?\n\n"
            "You MUST evaluate against the pre-agreed criteria, not "
            "introduce new ones.  The whole point of this method is that "
            "criteria were locked before evidence was gathered."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        state = discussion.method_state

        if new_phase.name == "criteria":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All positions are on the table.  Participants will now "
                "jointly define specific, testable criteria that would "
                "settle the disagreement.  These criteria will be LOCKED "
                "before evidence gathering begins."
            )

        if new_phase.name == "evidence":
            criteria = state.get("criteria", [])
            criteria_text = "\n".join(
                f"  **C{i+1}:** {c}" for i, c in enumerate(criteria)
            )
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "Settlement criteria are now LOCKED:\n"
                f"{criteria_text}\n\n"
                "Participants will gather evidence relevant to these "
                "criteria.  No new criteria may be introduced."
            )

        if new_phase.name == "adjudicate":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All evidence has been presented.  The moderator will now "
                "evaluate the evidence against the pre-agreed criteria "
                "and render a verdict."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        phase = self.current_phase(discussion)
        if not phase:
            return ProcessedResponse(display_content=content)
        state = discussion.method_state

        if phase.name == "positions":
            # Store a brief summary of the position keyed by entity name
            summary = content.strip()[:200]
            if len(content.strip()) > 200:
                summary += "..."
            state.setdefault("positions", {})[entity.name] = summary

        if phase.name == "criteria":
            new_criteria = self._parse_criteria(content)
            if new_criteria:
                existing = state.get("criteria", [])
                for c in new_criteria:
                    if c not in existing:
                        existing.append(c)
                state["criteria"] = existing

        return ProcessedResponse(display_content=content)

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
                        if len(m.strip()) > 10]
        return []

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "positions":
            return (bool(state.get("positions"))
                    and state.get("phase_round", 1) > 1)

        if phase.name == "criteria":
            return (bool(state.get("criteria"))
                    and state.get("phase_round", 1) > phase.rounds)

        return state.get("phase_round", 1) > phase.rounds
