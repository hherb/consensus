"""Delphi Method — anonymous iterative expert forecasting.

Participants provide independent estimates across multiple rounds.
After each round, the moderator shares the statistical distribution
and anonymised reasoning.  Participants then revise their estimates.
Avoids anchoring, authority bias, and social pressure.

Phases:
  1. ESTIMATE    — Each participant provides an independent estimate
                   with reasoning (round 1)
  2. REVISE      — Moderator shares distribution + anonymised reasoning;
                   participants revise (rounds 2+, until convergence)
  3. SYNTHESISE  — Moderator presents the final distribution and
                   analyses the convergence pattern
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from .base import DiscussionMethod, Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity

logger = logging.getLogger(__name__)

# Convergence: stop when the IQR (inter-quartile range) of numeric
# estimates falls below this fraction of the median.
DEFAULT_CONVERGENCE_RATIO = 0.15

# Maximum revision rounds
MAX_REVISE_ROUNDS = 5


class DelphiMethod(DiscussionMethod):
    """Delphi Method — anonymous iterative convergence."""

    name = "delphi"
    display_name = "Delphi Method"
    description = (
        "Anonymous iterative forecasting.  Participants provide independent "
        "estimates, then see the group's statistical distribution and "
        "anonymised reasoning before revising.  Avoids anchoring, authority "
        "bias, and social pressure.  Best for questions with quantifiable "
        "answers or probability estimates."
    )
    default_phases = (
        Phase(
            name="estimate",
            display_name="Initial Estimates",
            description=(
                "Each participant independently provides their estimate "
                "or assessment, with detailed reasoning."
            ),
            rounds=1,
        ),
        Phase(
            name="revise",
            display_name="Revision Rounds",
            description=(
                "After seeing the group's statistical distribution and "
                "anonymised reasoning, participants revise their estimates."
            ),
            rounds=0,  # condition-based (convergence)
        ),
        Phase(
            name="synthesise",
            display_name="Synthesis",
            description=(
                "The moderator presents the final distribution and "
                "analyses the convergence pattern."
            ),
            rounds=1,
        ),
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        state = super().init_state(discussion)
        state["estimates"] = []  # [{round, entity_id, entity_name, value, reasoning}]
        state["revise_round"] = 0
        state["max_revise_rounds"] = MAX_REVISE_ROUNDS
        state["convergence_ratio"] = DEFAULT_CONVERGENCE_RATIO
        return state

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        super().on_round_complete(discussion)
        phase = self.current_phase(discussion)
        if phase and phase.name == "revise":
            discussion.method_state["revise_round"] = (
                discussion.method_state.get("revise_round", 0) + 1
            )

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "estimate":
            return state.get("phase_round", 1) > 1

        if phase.name == "revise":
            revise_round = state.get("revise_round", 0)
            max_rounds = state.get("max_revise_rounds", MAX_REVISE_ROUNDS)
            if revise_round >= max_rounds:
                return True
            return self._check_convergence(discussion)

        if phase.name == "synthesise":
            return state.get("phase_round", 1) > 1

        return False

    def _check_convergence(self, discussion: Discussion) -> bool:
        """Check if estimates have converged."""
        state = discussion.method_state
        estimates = state.get("estimates", [])
        revise_round = state.get("revise_round", 0)
        threshold = state.get("convergence_ratio", DEFAULT_CONVERGENCE_RATIO)

        if revise_round < 1:
            return False

        # Get latest round's numeric estimates
        current_values = []
        for e in estimates:
            if e["round"] == revise_round and e.get("value") is not None:
                current_values.append(e["value"])

        if len(current_values) < 2:
            return False

        current_values.sort()
        median = current_values[len(current_values) // 2]
        if median == 0:
            return False

        # IQR / median
        q1 = current_values[len(current_values) // 4]
        q3 = current_values[3 * len(current_values) // 4]
        iqr = q3 - q1
        ratio = abs(iqr / median)

        converged = ratio < threshold
        if converged:
            logger.info(
                "Delphi converged (IQR/median=%.4f < threshold=%.4f)",
                ratio, threshold,
            )
        return converged

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""
        state = discussion.method_state

        base = (
            f"You are {entity.name}, participating in a Delphi Method "
            f"forecasting exercise.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "estimate":
            return base + (
                "INITIAL ESTIMATE PHASE\n\n"
                "Provide your independent estimate or assessment.  "
                "IMPORTANT: Do not anchor on others' views — this is your "
                "independent judgement.\n\n"
                "You MUST include a JSON block with your estimate:\n"
                "```json\n"
                '{"estimate": <number_or_probability>, '
                '"confidence": "<HIGH/MEDIUM/LOW>", '
                '"unit": "<what the number represents>"}\n'
                "```\n\n"
                "After the JSON, provide detailed reasoning:\n"
                "1. What evidence or reasoning supports your estimate?\n"
                "2. What are the key uncertainties?\n"
                "3. What would make you revise significantly upward or "
                "downward?\n\n"
                "If the question is not naturally numeric, provide a "
                "probability estimate (0.0 to 1.0) for the most likely "
                "outcome."
            )

        if phase.name == "revise":
            # Build anonymised summary of previous round
            summary = self._build_distribution_summary(discussion)
            round_num = state.get("revise_round", 0) + 1
            return base + (
                f"REVISION ROUND {round_num}\n\n"
                f"Here is the group's distribution from the previous "
                f"round (anonymised):\n{summary}\n\n"
                "Review the distribution and anonymised reasoning.  Then "
                "provide your REVISED estimate.\n\n"
                "You MUST include a JSON block:\n"
                "```json\n"
                '{"estimate": <number_or_probability>, '
                '"confidence": "<HIGH/MEDIUM/LOW>", '
                '"unit": "<what the number represents>"}\n'
                "```\n\n"
                "Explain:\n"
                "1. Has your estimate changed?  By how much and why?\n"
                "2. Which anonymised arguments were most persuasive?\n"
                "3. What reasoning do you maintain despite the group "
                "distribution?\n\n"
                "You are NOT obligated to move toward the group — only "
                "revise if the reasoning warrants it."
            )

        if phase.name == "synthesise":
            return ""  # moderator handles synthesis

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "estimate":
            return (
                f"It is your turn, {entity.name}.  Provide your "
                "independent estimate with the JSON block and reasoning."
            )

        if phase.name == "revise":
            round_num = discussion.method_state.get("revise_round", 0) + 1
            return (
                f"Revision round {round_num}, {entity.name}.  Review the "
                "group distribution and provide your revised estimate."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "estimate":
            return (
                f"An estimate has been received (details withheld to "
                f"preserve anonymity).  Next: {next_speaker_name}."
            )

        if phase.name == "revise":
            return (
                f"A revised estimate has been received.  "
                f"Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        summary = self._build_full_trajectory(discussion)

        return (
            "The Delphi Method process is complete.\n\n"
            f"Estimate trajectories:\n{summary}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Final distribution** — Report the median, mean, range, "
            "and inter-quartile range of the final estimates\n"
            "2. **Convergence analysis** — Did the group converge?  How "
            "much did estimates change from initial to final round?\n"
            "3. **Outlier analysis** — Were there persistent outliers?  "
            "Did their reasoning contain unique insights or errors?\n"
            "4. **Key arguments** — Which arguments were most influential "
            "in driving convergence or maintaining divergence?\n"
            "5. **Confidence assessment** — Based on the convergence "
            "pattern and reasoning quality, how confident should we be "
            "in the group estimate?\n"
            "6. **Final answer** — State the group's best estimate with "
            "uncertainty bounds.\n\n"
            "Present actual numbers and cite specific reasoning."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        state = discussion.method_state

        if new_phase.name == "revise":
            summary = self._build_distribution_summary(discussion)
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All initial estimates are in.  Here is the anonymised "
                f"distribution:\n{summary}\n\n"
                "Each participant will now revise their estimate after "
                "seeing the group's distribution and anonymised reasoning."
            )

        if new_phase.name == "synthesise":
            converged = self._check_convergence(discussion)
            reason = "converged" if converged else "reached the round limit"
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"The Delphi process has {reason}.  "
                "The moderator will now synthesise the final distribution."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        phase = self.current_phase(discussion)
        if not phase or phase.name not in ("estimate", "revise"):
            return ProcessedResponse(display_content=content)

        state = discussion.method_state
        estimate_data = self._extract_estimate(content)

        if estimate_data:
            if phase.name == "estimate":
                round_num = 0
            else:
                round_num = state.get("revise_round", 0) + 1

            entry = {
                "round": round_num,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "value": estimate_data.get("estimate"),
                "confidence": estimate_data.get("confidence", ""),
                "unit": estimate_data.get("unit", ""),
            }
            state.setdefault("estimates", []).append(entry)

            # Augment display
            val = estimate_data.get("estimate", "?")
            conf = estimate_data.get("confidence", "?")
            unit = estimate_data.get("unit", "")
            bar = f"\n\n---\n**Estimate:** {val} {unit} (Confidence: {conf})"
            display = content + bar
        else:
            logger.warning(
                "Could not extract estimate from %s's response",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data=estimate_data if estimate_data else {},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_estimate(self, content: str) -> dict:
        """Parse an estimate JSON block from the response."""
        # Try ```json block
        json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', content,
                               re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if "estimate" in data:
                    data["estimate"] = float(data["estimate"])
                    return data
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Fallback: inline JSON
        match = re.search(r'\{"estimate"\s*:.+?\}', content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                data["estimate"] = float(data["estimate"])
                return data
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        return {}

    def _build_distribution_summary(self, discussion: Discussion) -> str:
        """Build an anonymised summary of the latest round's estimates."""
        state = discussion.method_state
        estimates = state.get("estimates", [])

        if not estimates:
            return "(No estimates yet)"

        # Find the latest round
        latest_round = max(e["round"] for e in estimates)
        latest = [e for e in estimates if e["round"] == latest_round]

        if not latest:
            return "(No estimates for this round)"

        values = [e["value"] for e in latest if e.get("value") is not None]
        if not values:
            return "(No numeric estimates extracted)"

        values.sort()
        n = len(values)
        mean = sum(values) / n
        median = values[n // 2]
        low = values[0]
        high = values[-1]
        q1 = values[n // 4] if n >= 4 else low
        q3 = values[3 * n // 4] if n >= 4 else high

        unit = latest[0].get("unit", "") if latest else ""

        lines = [
            f"  Participants: {n}",
            f"  Mean: {mean:.4g} {unit}",
            f"  Median: {median:.4g} {unit}",
            f"  Range: [{low:.4g}, {high:.4g}] {unit}",
            f"  Inter-quartile range: [{q1:.4g}, {q3:.4g}] {unit}",
            "",
            "  Individual estimates (anonymised, sorted):",
        ]
        # Sort entries by value for anonymised display, preserving
        # the correct confidence for each entry even when values match.
        sorted_entries = sorted(
            [e for e in latest if e.get("value") is not None],
            key=lambda e: e["value"],
        )
        for i, entry in enumerate(sorted_entries):
            v = entry["value"]
            conf = entry.get("confidence", "")
            lines.append(f"    Panelist {i+1}: {v:.4g} {unit} "
                         f"(Confidence: {conf})")

        return "\n".join(lines)

    def _build_full_trajectory(self, discussion: Discussion) -> str:
        """Build a full trajectory of estimates across all rounds."""
        state = discussion.method_state
        estimates = state.get("estimates", [])

        if not estimates:
            return "(No data)"

        # Group by entity
        by_entity: dict[int, list[dict]] = {}
        for e in estimates:
            by_entity.setdefault(e["entity_id"], []).append(e)

        lines = []
        for eid, entries in by_entity.items():
            name = entries[0].get("entity_name", f"Entity {eid}")
            lines.append(f"  **{name}:**")
            for entry in sorted(entries, key=lambda e: e["round"]):
                label = ("Initial" if entry["round"] == 0
                         else f"Round {entry['round']}")
                val = entry.get("value", "?")
                conf = entry.get("confidence", "?")
                lines.append(f"    {label}: {val} ({conf})")

        return "\n".join(lines)
