"""Premortem Analysis — prospective hindsight method.

Assume a preliminary conclusion has been reached, then each participant
independently constructs a narrative of how and why it failed.  This
exploits the psychological finding that it is easier to explain a known
outcome than to critique a live idea (prospective hindsight).

Phases:
  1. FRAME        — Moderator states a preliminary conclusion or plan
  2. PREMORTEM    — Each participant imagines it failed and explains why
  3. CONSOLIDATE  — Moderator synthesises failure modes, identifies
                    the most plausible and most dangerous
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class PremortemAnalysis(DiscussionMethod):
    """Premortem Analysis — imagine failure before it happens."""

    name = "premortem"
    display_name = "Premortem Analysis"
    description = (
        "Assume a preliminary conclusion or plan is adopted, then each "
        "participant independently constructs a narrative of how and why "
        "it failed.  Psychologically easier than critiquing a live idea, "
        "this method surfaces risks and blind spots that normal discussion "
        "misses."
    )
    default_phases = (
        Phase(
            name="frame",
            display_name="Framing",
            description=(
                "The moderator states a preliminary conclusion or plan "
                "that will be subjected to premortem analysis."
            ),
            rounds=1,
        ),
        Phase(
            name="premortem",
            display_name="Premortem Narratives",
            description=(
                "Imagine we are one year in the future and the plan has "
                "FAILED spectacularly.  Write a narrative explaining how "
                "and why it failed.  Be specific and creative — identify "
                "concrete failure modes, not vague risks."
            ),
            rounds=2,
        ),
        Phase(
            name="consolidate",
            display_name="Consolidation",
            description=(
                "The moderator consolidates all failure narratives, "
                "identifies the most plausible and most dangerous failure "
                "modes, and recommends mitigations."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        state = super().init_state(discussion)
        state["conclusion"] = ""  # the plan/conclusion being analysed
        return state

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state
        conclusion = state.get("conclusion", "")

        base = (
            f"You are {entity.name}, participating in a Premortem Analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "frame":
            return ""  # moderator handles framing

        if phase.name == "premortem":
            return base + (
                "PREMORTEM PHASE\n\n"
                f"The following conclusion/plan has been tentatively adopted:\n"
                f"  \"{conclusion}\"\n\n"
                "IMAGINE IT IS ONE YEAR FROM NOW AND THIS PLAN HAS FAILED "
                "SPECTACULARLY.\n\n"
                "Your task:\n"
                "1. Write a vivid, specific narrative of HOW it failed\n"
                "2. Identify the root causes — what went wrong and why\n"
                "3. Note any early warning signs that were missed\n"
                "4. Be creative and consider failure modes others might overlook\n\n"
                "Do NOT hedge or soften — the plan has ALREADY failed in this "
                "scenario.  Your job is to explain why, not whether."
            )

        if phase.name == "consolidate":
            return ""  # moderator handles consolidation

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "premortem":
            round_num = discussion.method_state.get("phase_round", 1)
            if round_num == 1:
                return (
                    f"It is your turn, {entity.name}.  The plan has failed.  "
                    "Write your failure narrative — be specific and creative."
                )
            return (
                f"Round {round_num}, {entity.name}.  Having seen others' "
                "failure narratives, identify additional failure modes they "
                "missed, or elaborate on the most dangerous ones."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "premortem":
            return (
                f"{speaker_name} has presented their failure narrative.  "
                "Briefly note the key failure modes identified and how "
                "they differ from previously raised concerns.  "
                f"Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = state.get("conclusion", "")

        return (
            "The premortem analysis is complete.\n\n"
            f"Original plan/conclusion: \"{conclusion}\"\n\n"
            "Provide a comprehensive consolidation:\n"
            "1. **Failure mode inventory** — List ALL distinct failure modes "
            "identified across all narratives\n"
            "2. **Plausibility ranking** — Rank failure modes by likelihood, "
            "with brief justification\n"
            "3. **Severity assessment** — Which failures would be most "
            "damaging if they occurred?\n"
            "4. **Risk matrix** — Combine plausibility × severity to "
            "identify the highest-priority risks\n"
            "5. **Mitigations** — For each high-priority risk, suggest "
            "specific preventive measures or early warning indicators\n"
            "6. **Revised recommendation** — Should the plan proceed as-is, "
            "be modified, or be abandoned? What specific changes would "
            "address the most critical risks?\n\n"
            "Ground your analysis in the specific failure narratives "
            "provided by participants."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        state = discussion.method_state

        if new_phase.name == "premortem":
            conclusion = state.get("conclusion", "")
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "The following plan/conclusion will be subjected to "
                "premortem analysis:\n\n"
                f"> {conclusion}\n\n"
                "Imagine it is one year from now and this plan has "
                "**failed spectacularly**.  Each participant will explain "
                "how and why it failed."
            )

        if new_phase.name == "consolidate":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All failure narratives are in.  The moderator will now "
                "consolidate failure modes, rank them by plausibility and "
                "severity, and recommend mitigations."
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

        # Capture the conclusion from the moderator's framing message
        if phase.name == "frame" and not state.get("conclusion"):
            state["conclusion"] = content.strip()

        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "frame":
            return bool(state.get("conclusion"))

        return state.get("phase_round", 1) > phase.rounds
