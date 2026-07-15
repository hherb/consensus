"""Crux testing phase handler for Double Crux (issue #27).

A free-text, evidence-focused discussion of the shared crux alone: the
disagreement has been reduced to one checkable claim, so all evidence
and reasoning is directed at that claim — not the broader topic.  Opts
into evidence provenance tracking (``track_evidence=True``, issue #28):
each turn is classified as grounded or reasoning-based and the summary
feeds the conclusion and the ``crux_map`` artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler
from ._crux_helpers import TEST_CRUX_ROUNDS, format_shared_crux

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class TestCruxHandler(PhaseHandler):
    """Phase 4: Evidence and reasoning focused on the shared crux."""

    phase = Phase(
        name="test_crux",
        display_name="Crux Testing",
        description=(
            "The discussion focuses evidence and reasoning on the "
            "shared crux alone — the one claim the disagreement has "
            "been reduced to."
        ),
        rounds=TEST_CRUX_ROUNDS,
        track_evidence=True,
    )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"You are {entity.name}, participating in a Double Crux "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "CRUX TESTING PHASE\n\n"
            "The disagreement has been reduced to this crux:\n"
            f"{format_shared_crux(state)}\n\n"
            "Direct ALL evidence and reasoning at this claim alone:\n"
            "1. Present the strongest evidence you know for or against "
            "it — cite sources, data, or concrete experience, and use "
            "any research or document tools available to you\n"
            "2. Say what evidence would settle it either way\n"
            "3. Do NOT re-litigate the broader topic or restate your "
            "position — only the crux is under discussion\n\n"
            "Be genuinely truth-seeking: you named this claim as the "
            "one that would change your mind."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Present your strongest "
            "evidence and reasoning on the crux — and only the crux."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has presented evidence on the crux.  "
            "Briefly note what the evidence supports and where it is "
            "contested — keep the focus on the crux, not the broader "
            f"topic.  Next: {next_speaker_name}."
        )
