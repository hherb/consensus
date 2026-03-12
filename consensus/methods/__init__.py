"""Discussion method registry.

Each discussion method controls the structure, phases, prompts, and
synthesis logic of a discussion while reusing the core turn-taking and
message infrastructure.
"""

import json
import logging
from typing import Optional

from .base import DiscussionMethod, Phase, ProcessedResponse
from .open_discussion import OpenDiscussion
from .belief_diffusion import BeliefDiffusion
from .ach import ACH
from .premortem import PremortemAnalysis
from .key_assumptions import KeyAssumptionsCheck
from .adversarial_collab import AdversarialCollaboration
from .red_team import RedTeamBlueTeam
from .delphi import DelphiMethod
from .voting import VotingMethod
from .counterfactual import CounterfactualStressTest
from .recursive_decomposition import RecursiveDecomposition
from .triage import TriageMethod
from .self_distillation import RecursiveSelfDistillation

logger = logging.getLogger(__name__)

# Method registry — maps method name → singleton instance
_METHODS: dict[str, type[DiscussionMethod]] = {
    "open_discussion": OpenDiscussion,
    "belief_diffusion": BeliefDiffusion,
    "ach": ACH,
    "premortem": PremortemAnalysis,
    "key_assumptions": KeyAssumptionsCheck,
    "adversarial_collab": AdversarialCollaboration,
    "red_team": RedTeamBlueTeam,
    "delphi": DelphiMethod,
    "voting": VotingMethod,
    "counterfactual": CounterfactualStressTest,
    "recursive_decomposition": RecursiveDecomposition,
    "triage": TriageMethod,
    "self_distillation": RecursiveSelfDistillation,
}
_INSTANCES: dict[str, DiscussionMethod] = {}
_METHODS_METADATA: list[dict] | None = None


def get_method(name: str) -> DiscussionMethod:
    """Return a DiscussionMethod singleton by name.

    Raises ``KeyError`` if the name is not registered.
    """
    if name in _INSTANCES:
        return _INSTANCES[name]
    cls = _METHODS.get(name)
    if cls is None:
        raise KeyError(f"Unknown discussion method: {name!r}")
    instance = cls()
    _INSTANCES[name] = instance
    return instance


def list_methods() -> list[dict]:
    """Return metadata for all registered methods (cached)."""
    global _METHODS_METADATA
    if _METHODS_METADATA is None:
        _METHODS_METADATA = [get_method(name).to_dict()
                             for name in _METHODS]
    return _METHODS_METADATA


def get_active_method(discussion) -> Optional[DiscussionMethod]:
    """Return the DiscussionMethod for a discussion, or None for open.

    This is the canonical helper used by moderator.py and
    app_discussion_flow.py to avoid duplicating the lookup logic.
    """
    name = discussion.discussion_method
    if not name or name == "open_discussion":
        return None
    try:
        return get_method(name)
    except KeyError:
        logger.warning("Unknown discussion method %r, falling back to open",
                       name)
        return None


def serialize_method_state(method_state: dict) -> str:
    """Safely serialize method_state to JSON for DB persistence."""
    try:
        return json.dumps(method_state)
    except (TypeError, ValueError):
        logger.error("Failed to serialize method_state, resetting to {}",
                     exc_info=True)
        return "{}"


__all__ = [
    "DiscussionMethod", "Phase", "ProcessedResponse",
    "get_method", "get_active_method", "list_methods",
    "serialize_method_state",
    "OpenDiscussion", "BeliefDiffusion", "ACH",
    "PremortemAnalysis", "KeyAssumptionsCheck",
    "AdversarialCollaboration", "RedTeamBlueTeam", "DelphiMethod",
    "VotingMethod",
    "CounterfactualStressTest",
    "RecursiveDecomposition",
    "TriageMethod",
    "RecursiveSelfDistillation",
]
