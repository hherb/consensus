"""Analysis of Competing Hypotheses (ACH) — structured analytic method.

Developed by Richards Heuer for the CIA, ACH is designed to overcome
cognitive biases in analytical judgement.  Rather than seeking evidence
to confirm a favoured hypothesis, ACH evaluates ALL hypotheses against
ALL evidence simultaneously, focusing on *disconfirming* evidence.

Phases:
  1. HYPOTHESIZE — All participants propose competing hypotheses
  2. EVIDENCE    — Participants gather evidence (using tools)
  3. EVALUATE    — Each participant rates each hypothesis against each
                   piece of evidence: consistent (+), inconsistent (-),
                   or neutral (0)
  4. ANALYSE     — Moderator ranks hypotheses by inconsistency count,
                   identifies diagnostic evidence, checks for sensitivity
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

# Minimum character length for parsed text to be considered meaningful
MIN_HYPOTHESIS_LENGTH = 10
MIN_EVIDENCE_LENGTH = 15
# Word overlap ratio above which two hypotheses are considered duplicates
SIMILARITY_THRESHOLD = 0.7


class ACH(DiscussionMethod):
    """Analysis of Competing Hypotheses."""

    name = "ach"
    display_name = "Analysis of Competing Hypotheses"
    description = (
        "A structured analytic method from intelligence analysis.  "
        "Participants generate hypotheses, gather evidence, then "
        "systematically rate each hypothesis against each piece of "
        "evidence.  Hypotheses are ranked by inconsistency count — "
        "the one with the fewest inconsistencies is most likely."
    )
    default_phases = [
        Phase(
            name="hypothesize",
            display_name="Hypothesis Generation",
            description=(
                "Each participant proposes 2-3 competing hypotheses that "
                "could explain or answer the question.  Be creative — "
                "include hypotheses you disagree with."
            ),
            rounds=1,
        ),
        Phase(
            name="evidence",
            display_name="Evidence Gathering",
            description=(
                "Gather evidence relevant to the hypotheses.  Use tools "
                "(web search, documents, etc.) to find concrete facts.  "
                "Tag each piece of evidence with which hypotheses it "
                "supports or contradicts."
            ),
            rounds=2,
            allow_tools=True,
        ),
        Phase(
            name="evaluate",
            display_name="Matrix Evaluation",
            description=(
                "Rate each hypothesis against each piece of evidence.  "
                "Focus especially on INCONSISTENCIES — evidence that "
                "contradicts a hypothesis is more diagnostic than evidence "
                "that supports it."
            ),
            rounds=1,
        ),
        Phase(
            name="analyse",
            display_name="Analysis",
            description=(
                "The moderator analyses the evidence matrix, ranks "
                "hypotheses, identifies the most diagnostic evidence, "
                "and checks for sensitivity to key assumptions."
            ),
            rounds=1,
        ),
    ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Initialise ACH state."""
        return {
            "current_phase": "hypothesize",
            "phase_round": 1,
            "hypotheses": [],
            "evidence": [],  # [{id, text, source, contributor, contributor_id}]
            "matrix": {},    # {entity_id: {H_idx: {E_idx: "+"/"-"/"0"}}}
            "next_evidence_id": 1,
        }

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def should_advance_phase(self, discussion: Discussion) -> bool:
        """Check phase completion."""
        phase = self.current_phase(discussion)
        if not phase:
            return False
        state = discussion.method_state

        if phase.name == "hypothesize":
            return bool(state.get("hypotheses")) and state.get("phase_round", 1) > 1

        if phase.name == "evidence":
            return state.get("phase_round", 1) > phase.rounds

        if phase.name == "evaluate":
            return state.get("phase_round", 1) > 1

        if phase.name == "analyse":
            return state.get("phase_round", 1) > 1

        return False

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
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

        if phase.name == "hypothesize":
            return base + (
                "HYPOTHESIS GENERATION PHASE\n\n"
                "Propose 2-3 competing hypotheses that could explain or "
                "answer the question.  IMPORTANT: include hypotheses you "
                "disagree with — ACH requires evaluating ALL plausible "
                "explanations.\n\n"
                "Format each hypothesis on its own line:\n"
                "1. <hypothesis text>\n"
                "2. <hypothesis text>\n"
                "3. <hypothesis text>\n\n"
                "For each, provide 1-2 sentences of context about why "
                "it is plausible."
            )

        if phase.name == "evidence":
            hyp_list = "\n".join(f"  H{i+1}: {h}"
                                 for i, h in enumerate(hypotheses))
            return base + (
                "EVIDENCE GATHERING PHASE\n\n"
                f"Hypotheses under evaluation:\n{hyp_list}\n\n"
                "Find and present concrete evidence relevant to these "
                "hypotheses.  Use web search and other tools actively.\n\n"
                "For each piece of evidence, indicate:\n"
                "- The evidence itself (factual, specific)\n"
                "- Its source\n"
                "- Which hypotheses it supports (+) or contradicts (-)\n\n"
                "Format:\n"
                "**E1:** <evidence text> (Source: <source>)\n"
                "  Supports: H1, H3 | Contradicts: H2\n\n"
                "Focus on finding DISCONFIRMING evidence — evidence that "
                "rules out hypotheses is more valuable than evidence that "
                "supports them."
            )

        if phase.name == "evaluate":
            hyp_list = "\n".join(f"  H{i+1}: {h}"
                                 for i, h in enumerate(hypotheses))
            evidence = state.get("evidence", [])
            ev_list = "\n".join(
                f"  E{e['id']}: {e['text']} (Source: {e.get('source', '?')})"
                for e in evidence
            )
            return base + (
                "MATRIX EVALUATION PHASE\n\n"
                f"Hypotheses:\n{hyp_list}\n\n"
                f"Evidence:\n{ev_list}\n\n"
                "Rate EACH hypothesis against EACH piece of evidence.\n\n"
                "Use these ratings:\n"
                "  + (consistent) — the evidence is what you'd expect if "
                "this hypothesis were true\n"
                "  - (inconsistent) — the evidence contradicts or is unlikely "
                "under this hypothesis\n"
                "  0 (neutral) — the evidence doesn't meaningfully "
                "differentiate\n\n"
                "Output your ratings as a JSON matrix:\n"
                "```json\n"
                '{"ratings": {"H1": {"E1": "+", "E2": "-", ...}, '
                '"H2": {"E1": "0", ...}, ...}}\n'
                "```\n\n"
                "After the matrix, briefly explain your most important "
                "inconsistency ratings — why does that evidence contradict "
                "that hypothesis?"
            )

        if phase.name == "analyse":
            return ""  # moderator handles analysis

        return ""

    def get_turn_prompt(self, entity: Entity, discussion: Discussion) -> str:
        """Return phase-specific turn instruction."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "hypothesize":
            return (
                f"It is your turn, {entity.name}.  Propose 2-3 competing "
                "hypotheses.  Include at least one you personally doubt."
            )

        if phase.name == "evidence":
            round_num = discussion.method_state.get("phase_round", 1)
            return (
                f"Evidence gathering round {round_num}.  {entity.name}, "
                "find concrete evidence relevant to the hypotheses.  "
                "Use your tools to search for facts."
            )

        if phase.name == "evaluate":
            return (
                f"{entity.name}, evaluate each hypothesis against each piece "
                "of evidence using the +/-/0 rating system.  Include the "
                "JSON matrix."
            )

        return ""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return phase-aware summary prompt."""
        phase = self.current_phase(discussion)
        if not phase:
            return ""

        if phase.name == "hypothesize":
            return (
                f"{speaker_name} has proposed their hypotheses.  "
                "Briefly note the hypotheses and how they complement or "
                "overlap with previously proposed ones.  "
                f"Next: {next_speaker_name}."
            )

        if phase.name == "evidence":
            return (
                f"{speaker_name} has presented evidence.  Briefly note "
                "the most significant findings and which hypotheses they "
                f"affect.  Next: {next_speaker_name}."
            )

        if phase.name == "evaluate":
            return (
                f"{speaker_name} has submitted their evaluation matrix.  "
                "Note any ratings that differ significantly from previous "
                f"evaluators.  Next: {next_speaker_name}."
            )

        return ""

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Return the ACH analysis prompt."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])
        matrix = state.get("matrix", {})

        hyp_list = "\n".join(f"  H{i+1}: {h}"
                             for i, h in enumerate(hypotheses))
        ev_list = "\n".join(
            f"  E{e['id']}: {e['text']}"
            for e in evidence
        )

        # Aggregate matrix
        agg = self._aggregate_matrix(discussion)

        return (
            "The ACH evaluation is complete.  Provide a comprehensive "
            "analysis.\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Evidence:\n{ev_list}\n\n"
            f"Aggregated ratings (majority vote):\n{agg}\n\n"
            "Your analysis MUST include:\n"
            "1. **Hypothesis ranking** — Rank by inconsistency count "
            "(FEWER inconsistencies = more likely).  This is the core "
            "ACH insight: we reject hypotheses that are inconsistent "
            "with evidence, not confirm ones that are consistent.\n"
            "2. **Diagnostic evidence** — Which evidence items are most "
            "diagnostic (differentiate between hypotheses)?  Which are "
            "non-diagnostic (consistent with everything)?\n"
            "3. **Sensitivity analysis** — Would the ranking change if "
            "any single piece of evidence were removed?  If so, that "
            "evidence is a critical dependency.\n"
            "4. **Evaluator disagreements** — Where did participants "
            "disagree on ratings?  What does that tell us?\n"
            "5. **Conclusion** — Which hypothesis best survives scrutiny "
            "and why?  What would we need to investigate further?\n\n"
            "Ground your analysis in the data.  Do not speculate beyond "
            "what the evidence matrix supports."
        )

    def get_phase_transition_message(self, new_phase: Phase,
                                     discussion: Discussion) -> str:
        """Return a message announcing the phase transition."""
        state = discussion.method_state

        if new_phase.name == "evidence":
            hypotheses = state.get("hypotheses", [])
            hyp_list = "\n".join(f"  **H{i+1}:** {h}"
                                 for i, h in enumerate(hypotheses))
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "The following hypotheses will be evaluated:\n"
                f"{hyp_list}\n\n"
                "Participants will now gather evidence.  Focus on finding "
                "evidence that DISPROVES hypotheses — that is the key "
                "insight of ACH."
            )

        if new_phase.name == "evaluate":
            evidence = state.get("evidence", [])
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                f"{len(evidence)} pieces of evidence have been gathered.  "
                "Each participant will now rate every hypothesis against "
                "every piece of evidence using +/-/0 ratings."
            )

        if new_phase.name == "analyse":
            return (
                f"**Phase: {new_phase.display_name}**\n\n"
                "All evaluations are in.  The moderator will now analyse "
                "the evidence matrix and rank the hypotheses."
            )

        return super().get_phase_transition_message(new_phase, discussion)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract structured data from phase-specific responses."""
        phase = self.current_phase(discussion)
        if not phase:
            return ProcessedResponse(display_content=content)
        state = discussion.method_state

        if phase.name == "hypothesize":
            return self._process_hypotheses(content, entity, discussion)

        if phase.name == "evidence":
            return self._process_evidence(content, entity, discussion)

        if phase.name == "evaluate":
            return self._process_evaluation(content, entity, discussion)

        return ProcessedResponse(display_content=content)

    def _process_hypotheses(self, content: str, entity: Entity,
                            discussion: Discussion) -> ProcessedResponse:
        """Extract hypotheses from a participant's response."""
        state = discussion.method_state
        new_hyps = self._parse_hypotheses(content)

        if new_hyps:
            existing = state.get("hypotheses", [])
            # Deduplicate by checking for substantial overlap
            for h in new_hyps:
                if not any(self._similar(h, e) for e in existing):
                    existing.append(h)
            state["hypotheses"] = existing

        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_hypotheses": new_hyps},
        )

    def _process_evidence(self, content: str, entity: Entity,
                          discussion: Discussion) -> ProcessedResponse:
        """Extract evidence items from a participant's response."""
        state = discussion.method_state
        evidence_items = self._parse_evidence(content)

        for item in evidence_items:
            eid = state.get("next_evidence_id", 1)
            item["id"] = eid
            item["contributor"] = entity.name
            item["contributor_id"] = entity.id
            state.setdefault("evidence", []).append(item)
            state["next_evidence_id"] = eid + 1

        return ProcessedResponse(
            display_content=content,
            extracted_data={"evidence_count": len(evidence_items)},
        )

    def _process_evaluation(self, content: str, entity: Entity,
                            discussion: Discussion) -> ProcessedResponse:
        """Extract the rating matrix from a participant's evaluation."""
        state = discussion.method_state
        ratings = self._parse_ratings(content)

        if ratings:
            state.setdefault("matrix", {})[str(entity.id)] = ratings

            # Augment display with formatted matrix
            matrix_text = self._format_rating_matrix(ratings, discussion)
            display = f"{content}\n\n---\n{matrix_text}"
        else:
            logger.warning(
                "Could not extract ratings from %s's evaluation",
                entity.name,
            )
            display = content

        return ProcessedResponse(
            display_content=display,
            extracted_data={"ratings": ratings},
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_hypotheses(self, content: str) -> list[str]:
        """Extract numbered hypotheses from content."""
        patterns = [
            r'^\s*\d+[\.\)]\s*(.+)',
            r'^\s*H\d+[\.\):]\s*(.+)',
            r'^\s*[-*]\s+(.+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            if matches:
                return [m.strip().rstrip('.') for m in matches
                        if len(m.strip()) > MIN_HYPOTHESIS_LENGTH]
        return []

    def _parse_evidence(self, content: str) -> list[dict]:
        """Extract evidence items from content."""
        items: list[dict] = []

        # Pattern: **E1:** <text> (Source: <source>)
        pattern = r'\*\*E\d+:\*\*\s*(.+?)(?:\(Source:\s*(.+?)\))?$'
        for match in re.finditer(pattern, content, re.MULTILINE):
            text = match.group(1).strip()
            source = match.group(2).strip() if match.group(2) else ""
            if text:
                items.append({"text": text, "source": source})

        # Fallback: numbered items with evidence-like content
        if not items:
            for match in re.finditer(
                r'^\s*\d+[\.\)]\s*(.+?)(?:\(Source:\s*(.+?)\))?$',
                content, re.MULTILINE,
            ):
                text = match.group(1).strip()
                source = match.group(2).strip() if match.group(2) else ""
                if len(text) > MIN_EVIDENCE_LENGTH:
                    items.append({"text": text, "source": source})

        return items

    def _parse_ratings(self, content: str) -> dict[str, dict[str, str]]:
        """Extract the rating matrix JSON from content."""
        # Try ```json block
        json_match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', content,
                               re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if "ratings" in data:
                    return data["ratings"]
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: inline JSON
        match = re.search(r'\{"ratings"\s*:\s*\{.+?\}\s*\}', content,
                          re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data.get("ratings", {})
            except (json.JSONDecodeError, ValueError):
                pass

        return {}

    def _similar(self, h1: str, h2: str) -> bool:
        """Check if two hypotheses are substantially similar."""
        # Simple word overlap check
        w1 = set(h1.lower().split())
        w2 = set(h2.lower().split())
        if not w1 or not w2:
            return False
        overlap = len(w1 & w2) / max(len(w1), len(w2))
        return overlap > SIMILARITY_THRESHOLD

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_rating_matrix(self, ratings: dict,
                              discussion: Discussion) -> str:
        """Format a single evaluator's rating matrix as markdown."""
        hypotheses = discussion.method_state.get("hypotheses", [])
        evidence = discussion.method_state.get("evidence", [])

        if not ratings or not evidence:
            return ""

        lines = ["**Rating Matrix:**", ""]

        # Header
        e_headers = " | ".join(f"E{e['id']}" for e in evidence)
        lines.append(f"| | {e_headers} |")
        lines.append(f"|---|{'---|' * len(evidence)}")

        # Rows
        for hi, h in enumerate(hypotheses):
            key = f"H{hi+1}"
            h_ratings = ratings.get(key, {})
            cells = []
            for e in evidence:
                ekey = f"E{e['id']}"
                r = h_ratings.get(ekey, "?")
                cells.append(r)
            lines.append(f"| **{key}** | {' | '.join(cells)} |")

        return "\n".join(lines)

    def _aggregate_matrix(self, discussion: Discussion) -> str:
        """Aggregate all evaluators' matrices by majority vote."""
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        evidence = state.get("evidence", [])
        all_matrices = state.get("matrix", {})

        if not all_matrices or not evidence:
            return "(No evaluation data)"

        lines = []
        inconsistency_counts: dict[str, int] = {}

        for hi, h in enumerate(hypotheses):
            hkey = f"H{hi+1}"
            inc_count = 0
            row_parts = []
            for e in evidence:
                ekey = f"E{e['id']}"
                votes = {"+" : 0, "-": 0, "0": 0}
                for _evaluator_id, matrix in all_matrices.items():
                    r = matrix.get(hkey, {}).get(ekey, "0")
                    if r in votes:
                        votes[r] += 1
                # Majority vote across evaluators
                majority = max(votes, key=lambda k: votes[k])
                row_parts.append(majority)
                if majority == "-":
                    inc_count += 1

            inconsistency_counts[hkey] = inc_count
            row_str = ", ".join(
                f"E{e['id']}:{r}" for e, r in zip(evidence, row_parts)
            )
            lines.append(f"  {hkey}: [{row_str}] — {inc_count} inconsistencies")

        # Rank
        lines.append("")
        lines.append("  Ranking (fewer inconsistencies = more likely):")
        for hkey, count in sorted(inconsistency_counts.items(),
                                  key=lambda x: x[1]):
            try:
                idx = int(hkey.lstrip("H")) - 1
            except ValueError:
                idx = -1
            label = hypotheses[idx] if 0 <= idx < len(hypotheses) else hkey
            lines.append(f"    {hkey} ({count} inc.): {label}")

        return "\n".join(lines)
