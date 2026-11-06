"""Guard the ``methods.base`` re-exports of ``methods.phase_types`` (#61).

124 modules import ``Phase`` & co. from ``methods.base``; pin each
re-export by identity so a crossed or dropped name fails here.
"""

from consensus.methods import base, phase_types

MOVED = ("LINEAR_NEXT", "OutputToolSpec", "Phase", "ProcessedResponse")


def test_base_reexports_phase_types_by_identity() -> None:
    """Every moved name resolves to the very object in ``phase_types``."""
    for name in MOVED:
        assert getattr(base, name) is getattr(phase_types, name), name


def test_base_all_lists_every_public_name() -> None:
    """``__all__`` covers the moved names plus what ``base`` still defines."""
    assert set(base.__all__) == {
        *MOVED, "DiscussionMethod", "MAX_PHASE_VISITS_PER_PHASE",
    }
    for name in base.__all__:
        assert hasattr(base, name), name
