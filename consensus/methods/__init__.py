"""Discussion method registry.

Each discussion method controls the structure, phases, prompts, and
synthesis logic of a discussion while reusing the core turn-taking and
message infrastructure.
"""

from .base import DiscussionMethod, Phase, ProcessedResponse
from .open_discussion import OpenDiscussion
from .belief_diffusion import BeliefDiffusion
from .ach import ACH

# Method registry — maps method name → class
_METHODS: dict[str, type[DiscussionMethod]] = {
    "open_discussion": OpenDiscussion,
    "belief_diffusion": BeliefDiffusion,
    "ach": ACH,
}


def get_method(name: str) -> DiscussionMethod:
    """Return a DiscussionMethod instance by name.

    Raises ``KeyError`` if the name is not registered.
    """
    cls = _METHODS.get(name)
    if cls is None:
        raise KeyError(f"Unknown discussion method: {name!r}")
    return cls()


def list_methods() -> list[dict]:
    """Return metadata for all registered methods."""
    return [
        {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "phases": [
                {"name": p.name, "display_name": p.display_name,
                 "description": p.description}
                for p in cls.default_phases
            ],
        }
        for cls in _METHODS.values()
    ]


__all__ = [
    "DiscussionMethod", "Phase", "ProcessedResponse",
    "get_method", "list_methods",
    "OpenDiscussion", "BeliefDiffusion", "ACH",
]
