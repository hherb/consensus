"""Double Crux — find the factual crux beneath a disagreement (issue #27).

Instead of sharpening positions (Court of Law, Adversarial
Collaboration, Red Team), Double Crux searches for the underlying
belief that actually drives the disagreement and focuses evidence on
that alone.  Belief shift on the crux is the success metric; when no
factual crux exists, a clean map ("the disagreement reduces to X" /
"this is a values difference") is the successful outcome.

Phases:
  1. POSITIONS  — Parties state positions (reused StatePositionsHandler)
  2. HUNT       — "What claim, if you were wrong, would change your mind?"
  3. IDENTIFY   — Moderator finds the shared crux (loops back if none yet)
  4. TEST       — Evidence and reasoning focused on the crux alone
  5. RESOLVE    — Final positions + belief restatement → crux map
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..evidence import build_evidence_summary, format_sources
from .base import DiscussionMethod
from .phases._crux_helpers import (
    VERDICT_FACTUAL,
    VERDICT_VALUES,
    format_belief_shifts,
    format_positions,
    format_resolutions,
    format_shared_crux,
)
from .phases.hunt_cruxes import HuntCruxesHandler
from .phases.identify_crux import IdentifyCruxHandler
from .phases.resolve_crux import ResolveCruxHandler
from .phases.state_positions import StatePositionsHandler
from .phases.test_crux import TestCruxHandler

if TYPE_CHECKING:
    from ..models import Discussion


def _format_evidence_basis(state: dict) -> str:
    """Render the grounded/reasoning-based basis for the conclusion."""
    summary = build_evidence_summary(state)
    if not summary["grounded"] and not summary["reasoning_based"]:
        return "Evidentiary basis: no contributions were logged."
    lines = [
        f"Grounded contributions ({summary['counts']['grounded']}):"
    ]
    for g in summary["grounded"]:
        src = format_sources(g["sources"]) or "(sources recorded)"
        lines.append(f"  - {g['entity_name']}: {src}")
    reasoning = summary["reasoning_based"]
    if reasoning:
        names = ", ".join(r["entity_name"] for r in reasoning)
        lines.append(
            f"Reasoning-based (no cited evidence): {names}")
    return "\n".join(lines)


class DoubleCrux(DiscussionMethod):
    """Double Crux — reduce a disagreement to its pivotal claim."""

    name = "double_crux"
    display_name = "Double Crux"
    description = (
        "Disagreement resolution that searches for the underlying "
        "factual claim (the crux) actually driving the disagreement.  "
        "Parties state positions, each names the claims that would "
        "change their mind, the moderator identifies a shared crux, "
        "evidence is focused on that crux alone, and each party states "
        "whether they updated.  Ends with either a resolution or a "
        "clean map: 'the disagreement reduces to X' or 'this is a "
        "values difference, not a factual one'.  Best for genuine "
        "disagreements where debate formats would only entrench "
        "positions."
    )
    phase_handlers = (
        StatePositionsHandler(context_label="a Double Crux session"),
        HuntCruxesHandler(),
        IdentifyCruxHandler(),
        TestCruxHandler(),
        ResolveCruxHandler(),
    )

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        verdict = state.get("crux_verdict", "")
        header = (
            "The Double Crux process is complete.\n\n"
            f"Initial positions:\n{format_positions(state)}\n\n"
            f"Final resolutions:\n{format_resolutions(state)}\n\n"
        )
        if verdict == VERDICT_FACTUAL:
            return header + (
                f"{format_shared_crux(state)}\n\n"
                "Belief shifts on the crux (initial → final):\n"
                f"{format_belief_shifts(state)}\n\n"
                f"{_format_evidence_basis(state)}\n\n"
                "Provide a comprehensive synthesis:\n"
                "1. **The crux** — State the shared crux and why the "
                "disagreement reduces to it\n"
                "2. **Evidence** — Summarise the strongest evidence "
                "presented on each side of the crux\n"
                "3. **Belief movement** — Report who updated, by how "
                "much, and on what evidence (use the actual numbers "
                "above)\n"
                "4. **Outcome** — Either the resolution reached, or the "
                "precise residual disagreement on the crux and what "
                "evidence would settle it\n"
                "5. **Next steps** — How the crux could be tested "
                "further."
            )
        if verdict == VERDICT_VALUES:
            return header + (
                f"{format_shared_crux(state)}\n\n"
                "The disagreement was found to be a values difference, "
                "not a factual one.  Provide a clean map:\n"
                "1. **The value difference** — State precisely what "
                "value or priority the disagreement reduces to\n"
                "2. **What is NOT disputed** — Note the factual ground "
                "the parties share\n"
                "3. **Positions restated** — Each party's position in "
                "terms of the value difference\n"
                "4. **Next steps** — How the parties can cooperate "
                "despite (or negotiate across) the value difference."
            )
        return header + (
            "No shared crux was found within the search budget.  "
            "Provide a clean map of the residual disagreement:\n"
            "1. **The candidate cruxes** — What each party said would "
            "change their mind, and why they did not overlap\n"
            "2. **The structure of the disagreement** — What it appears "
            "to reduce to, as precisely as the discussion allows\n"
            "3. **Positions restated** — Where each party now stands\n"
            "4. **Next steps** — What further hunting or reframing "
            "might yet surface a shared crux."
        )
