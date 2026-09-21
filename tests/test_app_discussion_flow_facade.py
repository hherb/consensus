"""Contract tests for the ``consensus.app_discussion_flow`` package facade.

Splitting the former single module into a package (issue #61) moved every
public name behind re-exports in ``__init__.py``.  Those re-exports are load
bearing in a way the rest of the suite cannot see: ``ConsensusApp`` reaches
the flow functions by *attribute access at call time* ::

    result = await app_discussion_flow.mediate(...)      # app.py

so a name dropped from ``__init__.py`` raises ``AttributeError`` on that route
in production rather than ``ImportError`` at import time.  Deleting five names
from the package namespace leaves the rest of the suite fully green, so
nothing else guards this.  These tests do.
"""

import ast
from pathlib import Path

import pytest

from consensus import app_discussion_flow

# The public flow API as of the #61 split.  Pinned deliberately: a name
# disappearing from this list is the regression these tests exist to catch,
# and a name appearing is a public-API addition that deserves a conscious
# update here rather than silently widening the facade.
EXPECTED_PUBLIC_API = {
    "apply_method_turn_order",
    "calculate_discussion_cost",
    "complete_turn",
    "conclude_discussion",
    "describe_turn_error",
    "generate_ai_turn",
    "handle_triage_handoff",
    "is_pass",
    "mediate",
    "method_roster",
    "reassign_turn",
    "refresh_ai_configs",
    "retry_method_switch",
    "stamp_turn_index",
    "submit_human_message",
    "submit_human_structured_message",
    "submit_moderator_message",
    "switch_discussion_method",
}


def _facade_attributes_used_by(module_path: Path) -> set[str]:
    """Return every ``app_discussion_flow.<name>`` attribute read in a module.

    Parses the source rather than importing it, so the result reflects what
    the code will actually look up at runtime even for branches the test
    suite never executes.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "app_discussion_flow"
    }


def test_all_matches_the_pinned_public_api() -> None:
    """``__all__`` is exactly the agreed public surface — no drift either way."""
    assert set(app_discussion_flow.__all__) == EXPECTED_PUBLIC_API


def test_all_names_are_importable_from_the_package() -> None:
    """Every name ``__all__`` advertises actually resolves on the package."""
    missing = [n for n in app_discussion_flow.__all__ if not hasattr(app_discussion_flow, n)]
    assert missing == [], f"__all__ advertises names the package does not export: {missing}"


def test_no_duplicate_entries_in_all() -> None:
    """``__all__`` is a set-like list; duplicates signal a bad merge."""
    names = list(app_discussion_flow.__all__)
    assert len(names) == len(set(names))


def test_consensusapp_call_sites_resolve() -> None:
    """Every ``app_discussion_flow.X`` that ``app.py`` calls is re-exported.

    This is the production failure mode the package split introduced: these
    are attribute reads, so a missing re-export surfaces only when the route
    is hit, and six of the nine call sites are never driven through
    ``ConsensusApp`` by the suite.
    """
    app_py = Path(__file__).resolve().parent.parent / "consensus" / "app.py"
    used = _facade_attributes_used_by(app_py)

    assert used, "found no app_discussion_flow.* call sites in app.py — test is stale"

    unresolved = sorted(n for n in used if not hasattr(app_discussion_flow, n))
    assert unresolved == [], (
        f"app.py calls app_discussion_flow.{{{','.join(unresolved)}}} "
        "but the package does not export them — this is an AttributeError "
        "waiting to happen on those routes"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_PUBLIC_API))
def test_public_name_is_callable(name: str) -> None:
    """Each re-export is the function itself, not a stray module or constant."""
    assert callable(getattr(app_discussion_flow, name))
