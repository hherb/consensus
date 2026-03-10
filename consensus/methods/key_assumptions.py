"""Key Assumptions Check — surface and challenge hidden assumptions.

Before analysis begins, participants explicitly identify the assumptions
underlying the question or prevailing view, then systematically challenge
each one.  Can function as a standalone method or as a mandatory first
phase in other methods.

Phases:
  1. SURFACE    — Each participant identifies key assumptions
  2. CHALLENGE  — Each participant challenges the surfaced assumptions
  3. ASSESS     — Moderator synthesises which assumptions hold, which
                   are vulnerable, and how this affects the analysis
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class KeyAssumptionsCheck(DiscussionMethod):
    """Key Assumptions Check — expose and test hidden assumptions."""

    name = "key_assumptions"
    display_name = "Key Assumptions Check"
    description = (
        "Explicitly surface the assumptions underlying the question or "
        "prevailing view, then systematically challenge each one.  "
        "Prevents analysis from being built on unexamined foundations.  "
        "Effective as a standalone method or as a first phase before "
        "deeper analysis."
    )
    default_phases = (
        Phase(
            name="surface",
            display_name="Surface Assumptions",
            description=(
                "Identify the key assumptions underlying the question, "
                "the prevailing view, or any proposed answer.  These may "
                "be factual, causal, logical, or value-based assumptions."
            ),
            rounds=1,
        ),
        Phase(
            name="challenge",
            display_name="Challenge Assumptions",
            description=(
                "Systematically challenge each surfaced assumption.  "
                "For each, ask: What evidence supports it?  Under what "
                "conditions would it be false?  What are the consequences "
                "if it is wrong?"
            ),
            rounds=1,
        ),
        Phase(
            name="assess",
            display_name="Assessment",
            description=(
                "The moderator assesses each assumption's status: "
                "confirmed, unsupported, or contested.  Identifies which "
                "vulnerable assumptions most affect the overall analysis."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        state = super().init_state(discussion)
        state["assumptions"] = []  # list of assumption strings
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
            f"You are {entity.name}, participating in a Key Assumptions Check.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "surface":
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
                "Format each assumption as a numbered item:\n"
                "1. <assumption>\n"
                "2. <assumption>\n"
                "...\n\n"
                "Aim for 3-5 assumptions.  Include assumptions that seem "
                "obvious — those are often the most dangerous when wrong."
            )

        if phase.name == "challenge":
            assumptions = state.get("assumptions", [])
            assumption_list = "\n".join(
                f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
            )
            return base + (
                "ASSUMPTION CHALLENGE PHASE\n\n"
                f"The following assumptions have been surfaced:\n"
                f"{assumption_list}\n\n"
                "For EACH assumption, provide:\n"
                "1. **Supporting evidence** — What evidence supports this "
                "assumption being true?\n"
                "2. **Falsification conditions** — Under what circumstances "
                "would this assumption be FALSE?\n"
                "3. **Consequences if wrong** — If this assumption is wrong, "
                "how would it change the analysis or conclusion?\n"
                "4. **Confidence rating** — Rate your confidence that this "
                "assumption holds: HIGH / MEDIUM / LOW\n\n"
                "Be rigorous — even assumptions you believe are correct "
                "deserve honest scrutiny."
            )

        if phase.name == "assess":
            return ""  # moderator handles assessment

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "surface":
            return (
                f"It is your turn, {entity.name}.  Identify 3-5 key "
                "assumptions underlying this topic.  Include both obvious "
                "and hidden assumptions."
            )

        if phase.name == "challenge":
            return (
                f"It is your turn, {entity.name}.  Systematically challenge "
                "each surfaced assumption with evidence, falsification "
                "conditions, consequences, and a confidence rating."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "surface":
            return (
                f"{speaker_name} has identified their key assumptions.  "
                "Briefly note which assumptions are new vs. overlapping "
                f"with previously surfaced ones.  Next: {next_speaker_name}."
            )

        if phase.name == "challenge":
            return (
                f"{speaker_name} has challenged the assumptions.  Note "
                "any assumptions rated LOW confidence and any surprising "
                f"findings.  Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        assumptions = state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
        )

        return (
            "The Key Assumptions Check is complete.\n\n"
            f"Assumptions examined:\n{assumption_list}\n\n"
            "Provide a comprehensive assessment:\n"
            "1. **Assumption status** — Classify each assumption as:\n"
            "   - CONFIRMED (strong evidence, high confidence)\n"
            "   - CONTESTED (mixed evidence, disagreement among participants)\n"
            "   - UNSUPPORTED (weak evidence, low confidence)\n"
            "   - REFUTED (strong counter-evidence)\n"
            "2. **Load-bearing assumptions** — Which assumptions, if wrong, "
            "would most change the overall analysis or conclusion?\n"
            "3. **Blind spots** — Were any important assumptions NOT surfaced "
            "that should have been?\n"
            "4. **Recommendations** — Given the assumption landscape, what "
            "should be investigated further before proceeding?  What "
            "conclusions should be held tentatively?\n\n"
            "Be specific and cite the challenges raised by participants."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        state = discussion.method_state

        if new_phase.name == "challenge":
            assumptions = state.get("assumptions", [])
            assumption_list = "\n".join(
                f"  **A{i+1}:** {a}" for i, a in enumerate(assumptions)
            )
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"{len(assumptions)} assumptions have been surfaced:\n"
                f"{assumption_list}\n\n"
                "Each participant will now systematically challenge these "
                "assumptions."
            )

        if new_phase.name == "assess":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All challenges are in.  The moderator will now assess "
                "each assumption's status and identify which vulnerable "
                "assumptions most affect the analysis."
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

        if phase.name == "surface":
            new_assumptions = self._parse_assumptions(content)
            if new_assumptions:
                existing = state.get("assumptions", [])
                for a in new_assumptions:
                    if not any(self._similar(a, e) for e in existing):
                        existing.append(a)
                state["assumptions"] = existing
            return ProcessedResponse(
                display_content=content,
                extracted_data={"new_assumptions": new_assumptions},
            )

        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_assumptions(self, content: str) -> list[str]:
        """Extract numbered assumptions from content."""
        patterns = [
            r'^\s*\d+[\.\)]\s*(.+)',
            r'^\s*A\d+[\.\):]\s*(.+)',
            r'^\s*[-*]\s+(.+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            if matches:
                return [m.strip().rstrip('.') for m in matches
                        if len(m.strip()) > 10]
        return []

    def _similar(self, a1: str, a2: str) -> bool:
        """Check if two assumptions are substantially similar."""
        w1 = set(a1.lower().split())
        w2 = set(a2.lower().split())
        if not w1 or not w2:
            return False
        overlap = len(w1 & w2) / max(len(w1), len(w2))
        return overlap > 0.7

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "surface":
            return (bool(state.get("assumptions"))
                    and state.get("phase_round", 1) > 1)

        return state.get("phase_round", 1) > phase.rounds
