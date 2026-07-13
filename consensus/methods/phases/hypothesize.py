"""Hypothesize phase handler for Analysis of Competing Hypotheses.

Participants propose competing hypotheses via the forced
``submit_hypotheses`` output tool (issue #23); free-text numbered-list
parsing remains as the fallback path for humans and non-tool turns.
Either way, hypotheses are deduplicated by word overlap similarity and
accumulate across participants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

# Minimum character length for a hypothesis to be considered meaningful
MIN_HYPOTHESIS_LENGTH = 10
# Word overlap ratio above which two hypotheses are considered duplicates
SIMILARITY_THRESHOLD = 0.7
# Give up and advance after this many rounds even without parsed
# hypotheses — an unparseable group must not loop forever (issue #15).
MAX_HYPOTHESIZE_ROUNDS = 3

#: JSON Schema for the submit_hypotheses output tool (issue #23).
#:
#: Declared here rather than reused from Belief Diffusion's
#: ``_belief_helpers.HYPOTHESES_TOOL_PARAMETERS`` because ACH's
#: semantics differ: ACH *accumulates* hypotheses across participants
#: over multiple rounds (each turn adds to the shared pool), whereas
#: Belief Diffusion's framing tool produces one bounded 3-5 hypothesis
#: set in a single moderator turn.  Accordingly this schema has no
#: minItems/maxItems bound — ``validate_hypotheses_payload`` only
#: requires at least one substantive hypothesis.
HYPOTHESES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {"type": "string"},
            "description": ("Competing hypotheses that could explain or "
                            "answer the question — include ones you "
                            "personally doubt; ACH requires evaluating "
                            "ALL plausible explanations."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Your rationale for these hypotheses: why "
                            "each is plausible and how they differ."),
        },
    },
    "required": ["hypotheses", "reasoning"],
}


def validate_hypotheses_payload(payload: dict) -> str:
    """Return '' if a submit_hypotheses payload is usable, else an error.

    Applies the same substantive-length bar as the free-text path
    (``parse_numbered_list`` with ``min_length=MIN_HYPOTHESIS_LENGTH``,
    which keeps items of length ``>= MIN_HYPOTHESIS_LENGTH``) — but,
    unlike Belief Diffusion's framing validator, does not enforce a
    fixed hypothesis-count range, since ACH accumulates hypotheses
    across participants and turns.
    """
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        return "'hypotheses' must be a non-empty array of hypothesis strings."
    for h in hypotheses:
        if not isinstance(h, str) or len(h.strip()) < MIN_HYPOTHESIS_LENGTH:
            return (
                "Each hypothesis must be a substantive string of at "
                f"least {MIN_HYPOTHESIS_LENGTH} characters describing a "
                f"specific, plausible explanation (got: {h!r})."
            )
    if not str(payload.get("reasoning", "")).strip():
        return "'reasoning' must contain your rationale for these hypotheses."
    return ""


class HypothesizeHandler(PhaseHandler):
    """Phase 1: Generate competing hypotheses."""

    phase = Phase(
        name="hypothesize",
        display_name="Hypothesis Generation",
        description=(
            "Each participant proposes 2-3 competing hypotheses that "
            "could explain or answer the question.  Be creative — "
            "include hypotheses you disagree with."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "hypotheses": [],
            "evidence": [],
            "matrix": {},
            "next_evidence_id": 1,
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        base = (
            f"You are {entity.name}, participating in an Analysis of "
            f"Competing Hypotheses (ACH) structured analysis.\n"
            f"Topic: {discussion.topic}\n\n"
        )
        return base + (
            "HYPOTHESIS GENERATION PHASE\n\n"
            "Propose 2-3 competing hypotheses that could explain or "
            "answer the question.  IMPORTANT: include hypotheses you "
            "disagree with — ACH requires evaluating ALL plausible "
            "explanations.\n\n"
            "Submit your hypotheses by calling the submit_hypotheses "
            "tool with an array of hypothesis strings — each a "
            "complete, specific statement, with 1-2 sentences of "
            "context about why it is plausible — plus your rationale "
            "in the 'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Propose 2-3 competing "
            "hypotheses.  Include at least one you personally doubt.  "
            "Submit them by calling the submit_hypotheses tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their hypotheses.  "
            "Briefly note the hypotheses and how they complement or "
            "overlap with previously proposed ones.  "
            f"Next: {next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_hyps = parse_numbered_list(content, min_length=MIN_HYPOTHESIS_LENGTH)

        if new_hyps:
            existing = state.get("hypotheses", [])
            for h in new_hyps:
                if not any(word_overlap_similar(h, e, threshold=SIMILARITY_THRESHOLD)
                           for e in existing):
                    existing.append(h)
            state["hypotheses"] = existing

        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        """Declare the forced submit_hypotheses tool for this phase."""
        return OutputToolSpec(
            name="submit_hypotheses",
            description=("Submit competing hypotheses as an array of "
                         "hypothesis strings, plus your reasoning."),
            parameters=HYPOTHESES_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        """Validate a submit_hypotheses payload via the shared function."""
        return validate_hypotheses_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        """Dedup submitted hypotheses against existing ones and append.

        Mirrors ``process_response``'s exact dedup rule: a submitted
        hypothesis is dropped if it is word-overlap similar (threshold
        ``SIMILARITY_THRESHOLD``) to any hypothesis already in
        ``state["hypotheses"]``.  Unlike Belief Diffusion's framing
        tool, hypotheses accumulate here across participants and
        rounds, so accepted items from earlier turns are never
        replaced.  The display renders the reasoning first, followed
        by a numbered list of only the hypotheses accepted this turn
        (i.e. excluding any submitted duplicates).
        """
        state = discussion.method_state
        # rstrip('.') mirrors parse_numbered_list so structured items
        # dedup against regex-parsed ones in mixed human/AI panels.
        normalized = (str(h).strip().rstrip(".")
                      for h in payload["hypotheses"])
        submitted = [h for h in normalized if h]
        existing = state.get("hypotheses", [])
        accepted = []
        for h in submitted:
            if not any(word_overlap_similar(h, e, threshold=SIMILARITY_THRESHOLD)
                       for e in existing):
                existing.append(h)
                accepted.append(h)
        state["hypotheses"] = existing

        reasoning = str(payload.get("reasoning", "")).strip()
        numbered = "\n".join(f"{i}. {h}" for i, h in enumerate(accepted, 1))
        display = f"{reasoning}\n\n{numbered}" if numbered else reasoning
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_HYPOTHESIZE_ROUNDS:
            return True
        return bool(state.get("hypotheses")) and phase_round > 1

    # ------------------------------------------------------------------
    # Transition message
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )
