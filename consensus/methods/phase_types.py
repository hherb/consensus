"""Shared vocabulary of the method/phase-handler protocol.

The dataclasses and sentinel that both ``DiscussionMethod`` (``base.py``)
and the ``PhaseHandler`` library exchange.  Split out of ``base.py``
for issue #61; ``base.py`` re-exports every name, so existing
``from .base import Phase`` imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Sentinel returned by ``PhaseHandler.next_phase`` to request the
#: default linear phase progression.  A dunder name so it can never
#: collide with a real phase name.
LINEAR_NEXT: str = "__linear__"


@dataclass
class Phase:
    """A named stage within a discussion method."""

    name: str  # machine name, e.g. "hypothesize"
    display_name: str  # human-readable, e.g. "Hypothesis Generation"
    description: str = ""  # shown to participants
    rounds: int = 1  # how many full rounds in this phase (0 = until condition)
    allow_tools: bool = True  # whether tool use is enabled
    track_evidence: bool = False  # opt-in evidence provenance tracking (#28)


@dataclass
class ProcessedResponse:
    """Result of post-processing a participant's raw response.

    ``display_content`` may differ from the original response (e.g. with
    appended visualisations) and is what gets stored as the message
    content.  Handlers that extract structured data (belief
    distributions, hypothesis ratings, ...) must write it into
    ``discussion.method_state`` themselves — the flow layer consumes
    nothing else from this object (issue #21).
    """

    display_content: str  # what gets stored as the message content


@dataclass
class OutputToolSpec:
    """Declares the forced output tool for a structured phase (issue #23).

    Phases that declare a spec have their AI turns generated through a
    forced tool call instead of free-text parsing: the model must call
    ``name`` with arguments matching ``parameters`` (a JSON Schema
    object), making extraction near-deterministic.
    """

    name: str  # e.g. "submit_estimate"
    description: str  # shown to the model as the tool description
    parameters: dict  # JSON Schema for the tool arguments

    def to_openai_schema(self) -> dict:
        """Return the OpenAI function-tool schema for this spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
