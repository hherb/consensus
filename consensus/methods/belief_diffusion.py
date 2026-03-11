"""Belief State Diffusion — a novel LLM-native discussion method.

Each participant maintains an explicit probability distribution over
competing hypotheses.  After each round, participants see others'
distributions and reasoning, then update their own beliefs.  The
moderator tracks convergence and identifies which arguments caused
the largest belief shifts.

This exploits a unique LLM capability: the ability to maintain and
update explicit probability estimates without the cognitive overhead
that makes this nearly impossible for human groups.

Phases:
  1. FRAME     — Moderator decomposes the question into hypotheses
  2. PRIOR     — Each participant states initial belief distribution
  3. DIFFUSE   — Iterative belief updating (auto-stops on convergence)
  4. DIAGNOSE  — Moderator analyses belief trajectories and arguments
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.frame_hypotheses import FrameHypothesesHandler
from .phases.prior_beliefs import PriorBeliefsHandler
from .phases.diffuse_beliefs import DiffuseBeliefsHandler
from .phases.diagnose_beliefs import DiagnoseHandler
from .phases._belief_helpers import build_trajectory_summary

if TYPE_CHECKING:
    from ..models import Discussion


class BeliefDiffusion(DiscussionMethod):
    """Belief State Diffusion — track and visualise belief convergence."""

    name = "belief_diffusion"
    display_name = "Belief State Diffusion"
    description = (
        "A novel method where each participant maintains explicit probability "
        "estimates over competing hypotheses.  Beliefs are updated each round "
        "based on others' reasoning.  The method tracks convergence, identifies "
        "the most persuasive arguments, and detects inconsistencies between "
        "stated reasoning and actual belief shifts."
    )
    phase_handlers = (
        FrameHypothesesHandler(),
        PriorBeliefsHandler(),
        DiffuseBeliefsHandler(),
        DiagnoseHandler(),
    )

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        """Increment phase round and track diffusion rounds separately."""
        super().on_round_complete(discussion)
        phase = self.current_phase(discussion)
        if phase and phase.name == "diffuse":
            discussion.method_state["diffuse_round"] = (
                discussion.method_state.get("diffuse_round", 0) + 1
            )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the diagnosis/conclusion prompt."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])

        trajectory = build_trajectory_summary(discussion)

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))

        return (
            "The Belief State Diffusion process is complete.\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Belief trajectories:\n{trajectory}\n\n"
            "Provide a comprehensive diagnosis:\n"
            "1. **Final consensus** — What is the group's final distribution? "
            "Compute the mean probability for each hypothesis.\n"
            "2. **Convergence analysis** — Did beliefs converge? Where does "
            "persistent disagreement remain?\n"
            "3. **Persuasion analysis** — Which arguments caused the largest "
            "belief shifts? Were any participants resistant to strong evidence?\n"
            "4. **Consistency check** — Did any participant's stated reasoning "
            "contradict their actual belief shift? (e.g., said an argument was "
            "compelling but didn't change their beliefs)\n"
            "5. **Conclusion** — What does the group's final belief distribution "
            "tell us about the original question?\n\n"
            "Be specific and cite the data."
        )
