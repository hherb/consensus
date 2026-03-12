"""Guided Triage — collaborative method selection meta-method.

Phases:
  1. INTAKE    — Moderator interviews human participants
  2. RECOMMEND — Moderator synthesizes and recommends methods
  3. CONFIRM   — Group confirms or adjusts the selection
"""

from __future__ import annotations

from .base import DiscussionMethod
from .phases.triage_intake import TriageIntakeHandler
from .phases.triage_recommend import TriageRecommendHandler
from .phases.triage_confirm import TriageConfirmHandler


class TriageMethod(DiscussionMethod):
    """Guided Triage — collaborative method selection."""

    name = "triage"
    display_name = "Guided Triage"
    description = (
        "Collaborative method selection: the moderator interviews "
        "participants about the problem type, decision context, and "
        "uncertainty structure, then recommends a discussion method "
        "for the group to confirm or adjust."
    )
    phase_handlers = (
        TriageIntakeHandler(),
        TriageRecommendHandler(),
        TriageConfirmHandler(),
    )
