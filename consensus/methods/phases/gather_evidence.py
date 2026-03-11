"""Evidence gathering phase handler for Analysis of Competing Hypotheses.

Participants gather concrete evidence relevant to the hypotheses.
Evidence items are extracted with auto-increment IDs and contributor info.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

# Minimum character length for evidence to be considered meaningful
MIN_EVIDENCE_LENGTH = 15


class GatherEvidenceHandler(PhaseHandler):
    """Phase 2: Gather evidence relevant to hypotheses."""

    phase = Phase(
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
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])

        base = (
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )

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

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        round_num = discussion.method_state.get("phase_round", 1)
        return (
            f"Evidence gathering round {round_num}.  {entity.name}, "
            "find concrete evidence relevant to the hypotheses.  "
            "Use your tools to search for facts."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented evidence.  Briefly note "
            "the most significant findings and which hypotheses they "
            f"affect.  Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        evidence_items = self._parse_evidence(content)

        if not evidence_items:
            logger.warning(
                "Could not extract evidence from %s's response "
                "(expected **E1:** or numbered format)",
                entity.name,
            )

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

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        hypotheses = state.get("hypotheses", [])
        hyp_list = "\n".join(f"  **H{i+1}:** {h}"
                             for i, h in enumerate(hypotheses))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The following hypotheses will be evaluated:\n"
            f"{hyp_list}\n\n"
            "Participants will now gather evidence.  Focus on finding "
            "evidence that DISPROVES hypotheses — that is the key "
            "insight of ACH."
        )

    # ------------------------------------------------------------------
    # Parsing helpers (ACH-specific)
    # ------------------------------------------------------------------

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
