"""Red Team / Blue Team with Rotation — structured adversarial analysis.

Each round, one participant is designated as the Red Team (attacker)
while the others form the Blue Team (constructors).  The Red Team sees
only the current conclusion and tries to break it — they do NOT
participate in construction.  The Red Team role rotates each round.

Phases:
  1. CONSTRUCT  — Blue Team builds an initial position (Red Team silent)
  2. ATTACK     — Red Team attacks the position; Blue Team defends
  3. REVISE     — Blue Team revises based on surviving attacks
  4. ASSESS     — Moderator evaluates what survived scrutiny
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases import (
    AssessRedTeamHandler,
    AttackHandler,
    ConstructHandler,
    ReviseRedTeamHandler,
)

if TYPE_CHECKING:
    from ..models import Discussion


class RedTeamBlueTeam(DiscussionMethod):
    """Red Team / Blue Team with rotating adversarial role."""

    name = "red_team"
    display_name = "Red Team / Blue Team"
    description = (
        "Rotating adversarial analysis.  Each round, one participant "
        "is designated as the Red Team (attacker) while the others "
        "construct and defend a position.  The Red Team role rotates, "
        "ensuring every perspective gets stress-tested."
    )
    phase_handlers = (
        ConstructHandler(),
        AttackHandler(),
        ReviseRedTeamHandler(),
        AssessRedTeamHandler(),
    )

    # ------------------------------------------------------------------
    # Cross-phase: conclusion prompt
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        return (
            "The Red Team / Blue Team analysis is complete.\n\n"
            "Provide a comprehensive assessment:\n"
            "1. **Successful attacks** — Which Red Team attacks could not "
            "be adequately defended?  These represent genuine weaknesses.\n"
            "2. **Failed attacks** — Which attacks were successfully "
            "rebutted?  These confirm strengths.\n"
            "3. **Position evolution** — How did the Blue Team's position "
            "change from initial construction to final revision?\n"
            "4. **Surviving claims** — What elements of the position "
            "survived Red Team scrutiny?\n"
            "5. **Residual vulnerabilities** — What weaknesses remain "
            "even after revision?\n"
            "6. **Robustness rating** — How robust is the final position?  "
            "Rate as: STRONG (survived most attacks), MODERATE (significant "
            "revisions needed), or WEAK (fundamental problems exposed).\n\n"
            "Base your assessment on the specific attacks and defenses "
            "presented."
        )
