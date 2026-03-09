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

import json
import logging
import re
from typing import TYPE_CHECKING, Optional

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity

logger = logging.getLogger(__name__)

# Convergence: stop when the maximum belief shift across all
# participants and hypotheses falls below this threshold.
DEFAULT_CONVERGENCE_THRESHOLD = 0.05

# Safety limit for diffusion rounds
MAX_DIFFUSE_ROUNDS = 10


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
    default_phases = [
        Phase(
            name="frame",
            display_name="Framing",
            description=(
                "The moderator will decompose the question into 3-5 competing "
                "hypotheses or possible answers for participants to evaluate."
            ),
            rounds=1,
        ),
        Phase(
            name="prior",
            display_name="Prior Beliefs",
            description=(
                "Each participant states their initial probability distribution "
                "over the hypotheses, with brief reasoning."
            ),
            rounds=1,
        ),
        Phase(
            name="diffuse",
            display_name="Belief Diffusion",
            description=(
                "Participants see each other's belief distributions and reasoning, "
                "then update their own.  Continues until beliefs converge or the "
                "round limit is reached."
            ),
            rounds=0,  # condition-based (convergence)
        ),
        Phase(
            name="diagnose",
            display_name="Diagnosis",
            description=(
                "The moderator analyses the full belief trajectory: which arguments "
                "caused the largest shifts, where disagreement persists, and whether "
                "stated reasoning is consistent with actual belief changes."
            ),
            rounds=1,
        ),
    ]

    # ------------------------------------------------------------------
    # State initialisation
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Initialise belief diffusion state."""
        return {
            "current_phase": "frame",
            "phase_round": 1,
            "hypotheses": [],
            "belief_history": [],  # [{round, entity_id, entity_name, beliefs, reasoning}]
            "convergence_threshold": DEFAULT_CONVERGENCE_THRESHOLD,
            "max_diffuse_rounds": MAX_DIFFUSE_ROUNDS,
            "diffuse_round": 0,
        }

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        """Check whether to advance to the next phase."""
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "frame":
            # Advance once hypotheses have been set
            return bool(state.get("hypotheses"))

        if phase.name == "prior":
            # Advance after one full round
            return state.get("phase_round", 1) > 1

        if phase.name == "diffuse":
            # Advance on convergence or round limit
            diffuse_round = state.get("diffuse_round", 0)
            max_rounds = state.get("max_diffuse_rounds", MAX_DIFFUSE_ROUNDS)
            if diffuse_round >= max_rounds:
                return True
            return self._check_convergence(discussion)

        if phase.name == "diagnose":
            return state.get("phase_round", 1) > 1

        return False

    def _check_convergence(self, discussion: Discussion) -> bool:
        """Check if beliefs have converged below threshold."""
        state = discussion.method_state
        history = state.get("belief_history", [])
        threshold = state.get("convergence_threshold",
                              DEFAULT_CONVERGENCE_THRESHOLD)

        if len(history) < 2:
            return False

        # Get the last two rounds
        current_round = state.get("diffuse_round", 0)
        if current_round < 2:
            return False

        current_beliefs = {}
        prev_beliefs = {}
        for entry in history:
            if entry["round"] == current_round:
                current_beliefs[entry["entity_id"]] = entry["beliefs"]
            elif entry["round"] == current_round - 1:
                prev_beliefs[entry["entity_id"]] = entry["beliefs"]

        if not current_beliefs or not prev_beliefs:
            return False

        # Max delta across all participants and hypotheses
        max_delta = 0.0
        for eid, beliefs in current_beliefs.items():
            prev = prev_beliefs.get(eid, {})
            for hyp, prob in beliefs.items():
                old_prob = prev.get(hyp, 0.0)
                max_delta = max(max_delta, abs(prob - old_prob))

        converged = max_delta < threshold
        if converged:
            logger.info(
                "Belief diffusion converged (max_delta=%.4f < threshold=%.4f)",
                max_delta, threshold,
            )
        return converged

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return phase-appropriate system prompt."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])

        base = (
            f"You are {entity.name}, a participant in a structured Belief "
            f"State Diffusion analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "frame":
            return ""  # moderator handles framing

        if phase.name == "prior":
            hyp_list = "\n".join(f"  {i+1}. {h}"
                                 for i, h in enumerate(hypotheses))
            return base + (
                "The following hypotheses have been identified:\n"
                f"{hyp_list}\n\n"
                "You must provide your INITIAL probability distribution over "
                "these hypotheses.  Your probabilities must sum to 1.0.\n\n"
                "Output format — you MUST include this JSON block:\n"
                "```json\n"
                '{"beliefs": {"H1": 0.XX, "H2": 0.XX, ...}}\n'
                "```\n"
                "Use the hypothesis labels H1, H2, etc.\n"
                "Follow the JSON with 2-3 sentences of reasoning."
            )

        if phase.name == "diffuse":
            hyp_list = "\n".join(f"  H{i+1}: {h}"
                                 for i, h in enumerate(hypotheses))
            # Show other participants' latest beliefs
            others_text = self._format_others_beliefs(entity, discussion)
            return base + (
                f"Hypotheses:\n{hyp_list}\n\n"
                f"Other participants' current beliefs:\n{others_text}\n\n"
                "Review the other participants' reasoning carefully.  Then "
                "provide your UPDATED probability distribution.\n\n"
                "You MUST include this JSON block:\n"
                "```json\n"
                '{"beliefs": {"H1": 0.XX, "H2": 0.XX, ...}}\n'
                "```\n"
                "Probabilities must sum to 1.0.\n\n"
                "After the JSON, explain:\n"
                "1. What changed in your beliefs and WHY\n"
                "2. Which argument(s) from others were most persuasive\n"
                "3. What concerns remain\n\n"
                "If nothing changed, explain why the new arguments didn't "
                "alter your assessment."
            )

        if phase.name == "diagnose":
            return ""  # moderator handles diagnosis

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return phase-specific turn instruction."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "prior":
            return (
                f"It is your turn, {entity.name}.  State your initial beliefs "
                "as a probability distribution over the hypotheses.  "
                "Include the JSON block and your reasoning."
            )

        if phase.name == "diffuse":
            round_num = discussion.method_state.get("diffuse_round", 0) + 1
            return (
                f"Diffusion round {round_num}.  It is your turn, {entity.name}.  "
                "Review others' beliefs and reasoning, then provide your "
                "updated probability distribution with explanation."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return phase-aware summary prompt for the moderator."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "prior":
            return (
                f"{speaker_name} has stated their initial beliefs.  "
                "Briefly note their position and any notable aspects "
                f"of their reasoning.  Next up: {next_speaker_name}."
            )

        if phase.name == "diffuse":
            round_num = discussion.method_state.get("diffuse_round", 0)
            return (
                f"Diffusion round {round_num}: {speaker_name} has updated "
                "their beliefs.  Briefly note the direction and magnitude "
                "of their shift, and what drove it.  "
                f"Next up: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the diagnosis/conclusion prompt."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        history = state.get("belief_history", [])

        # Build trajectory summary
        trajectory = self._build_trajectory_summary(discussion)

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

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        """Return a message announcing the phase transition."""
        state = discussion.method_state

        if new_phase.name == "prior":
            hypotheses = state.get("hypotheses", [])
            hyp_list = "\n".join(f"  **H{i+1}:** {h}"
                                 for i, h in enumerate(hypotheses))
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "The following hypotheses will be evaluated:\n"
                f"{hyp_list}\n\n"
                "Each participant will now state their initial probability "
                "distribution over these hypotheses (must sum to 1.0) with "
                "brief reasoning."
            )

        if new_phase.name == "diffuse":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All initial beliefs are in.  Each round, you will see "
                "other participants' beliefs and reasoning, then update "
                "your own distribution.  The process continues until beliefs "
                "converge or the round limit is reached."
            )

        if new_phase.name == "diagnose":
            converged = self._check_convergence(discussion)
            reason = "converged" if converged else "reached the round limit"
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"Belief diffusion has {reason}.  "
                "The moderator will now analyse the full belief trajectory."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract belief distribution from participant response."""
        phase = self.current_phase(discussion)
        if not phase or phase.name not in ("prior", "diffuse"):
            return ProcessedResponse(display_content=content)

        state = discussion.method_state
        beliefs = self._extract_beliefs(content, discussion)

        if beliefs:
            # Determine the round number
            if phase.name == "prior":
                round_num = 0  # initial priors
            else:
                round_num = state.get("diffuse_round", 0) + 1

            # Record in history
            entry = {
                "round": round_num,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "beliefs": beliefs,
            }
            state.setdefault("belief_history", []).append(entry)

            # Augment display with formatted beliefs
            belief_bar = self._format_belief_bar(beliefs, discussion)
            display = f"{content}\n\n---\n{belief_bar}"
        else:
            logger.warning(
                "Could not extract beliefs from %s's response in phase %s",
                entity.name, phase.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data={"beliefs": beliefs} if beliefs else {},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_beliefs(self, content: str,
                         discussion: Discussion) -> dict[str, float]:
        """Parse a belief JSON block from the response content."""
        # Try to find ```json ... ``` block
        json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', content,
                               re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if "beliefs" in data and isinstance(data["beliefs"], dict):
                    return {k: float(v) for k, v in data["beliefs"].items()}
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Fallback: look for inline JSON object with "beliefs" key
        belief_match = re.search(
            r'\{"beliefs"\s*:\s*\{[^}]+\}\s*\}', content)
        if belief_match:
            try:
                data = json.loads(belief_match.group(0))
                return {k: float(v) for k, v in data["beliefs"].items()}
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        return {}

    def _format_belief_bar(self, beliefs: dict[str, float],
                           discussion: Discussion) -> str:
        """Format beliefs as a visual bar chart in markdown."""
        hypotheses = discussion.method_state.get("hypotheses", [])
        lines = ["**Belief Distribution:**"]
        for key, prob in sorted(beliefs.items()):
            # Map H1 → hypothesis text
            idx = int(key.replace("H", "")) - 1 if key.startswith("H") else -1
            label = hypotheses[idx] if 0 <= idx < len(hypotheses) else key
            bar_len = int(prob * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            lines.append(f"  {key} ({prob:.0%}) {bar}  *{label}*")
        return "\n".join(lines)

    def _format_others_beliefs(self, entity: Entity,
                               discussion: Discussion) -> str:
        """Format other participants' latest beliefs for context."""
        state = discussion.method_state
        history = state.get("belief_history", [])
        hypotheses = state.get("hypotheses", [])

        if not history:
            return "(No beliefs recorded yet)"

        # Find the latest entry per entity (excluding current entity)
        latest: dict[int, dict] = {}
        for entry in history:
            eid = entry["entity_id"]
            if eid != entity.id:
                latest[eid] = entry

        if not latest:
            return "(No other participants have stated beliefs yet)"

        lines = []
        for eid, entry in latest.items():
            name = entry.get("entity_name", f"Entity {eid}")
            beliefs = entry.get("beliefs", {})
            probs = ", ".join(f"{k}: {v:.0%}" for k, v in
                              sorted(beliefs.items()))
            lines.append(f"  {name}: [{probs}]")
        return "\n".join(lines)

    def _build_trajectory_summary(self, discussion: Discussion) -> str:
        """Build a text summary of belief trajectories over all rounds."""
        state = discussion.method_state
        history = state.get("belief_history", [])

        if not history:
            return "(No data)"

        # Group by entity
        by_entity: dict[int, list[dict]] = {}
        for entry in history:
            by_entity.setdefault(entry["entity_id"], []).append(entry)

        lines = []
        for eid, entries in by_entity.items():
            name = entries[0].get("entity_name", f"Entity {eid}")
            lines.append(f"  **{name}:**")
            for entry in sorted(entries, key=lambda e: e["round"]):
                probs = ", ".join(f"{k}: {v:.0%}" for k, v in
                                  sorted(entry["beliefs"].items()))
                label = "Prior" if entry["round"] == 0 else f"Round {entry['round']}"
                lines.append(f"    {label}: [{probs}]")

        return "\n".join(lines)

    def extract_hypotheses_from_framing(self, content: str) -> list[str]:
        """Parse hypotheses from the moderator's framing message.

        Looks for numbered lists (1. ... 2. ...) or H1: ... H2: ... patterns.
        """
        hypotheses: list[str] = []

        # Pattern 1: numbered list
        numbered = re.findall(r'^\s*(?:H?\d+[\.\):])\s*(.+)', content,
                              re.MULTILINE)
        if numbered:
            return [h.strip().rstrip('.') for h in numbered if len(h.strip()) > 5]

        # Pattern 2: bold or markdown list items
        bullets = re.findall(r'^\s*[-*]\s*\*?\*?(.+?)\*?\*?\s*$', content,
                             re.MULTILINE)
        if bullets:
            return [h.strip().rstrip('.') for h in bullets if len(h.strip()) > 5]

        return hypotheses
